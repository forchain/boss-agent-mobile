"""
boss_agent.adapters.shared.records
===================================
Record-shape and merge rules shared by the two Job Record Store adapters.

These helpers decide what a job record looks like and how a newer observation folds
into an existing one: the canonical field set, the monotonic status ladder, the
fill-a-gap fields, and the sticky fields a re-scrape may enrich but never erase.

They live here rather than in the PocketBase adapter because the in-memory adapter
uses them too. Importing them from PocketBase dragged ``requests`` and a peer outbound
adapter into every in-memory store; this module has no HTTP or transport dependency.
"""

from __future__ import annotations

from typing import Any

from boss_agent.core.shared.enums import STATE_RANK, JobRecordStatus

#: Company names that mean "not a usable employer", as the stores see them.
INVALID_COMPANY_NAMES: frozenset[str] = frozenset({"", "未注明公司", "未知公司"})

#: Canonical non-primary-key wire defaults for ``job_records``, shared by both the
#: in-memory and PocketBase adapters without importing ``collection_schema``.
#: Keep in lockstep with ``JOB_RECORDS`` (enforced by ``test_collection_schema.py``).
JOB_RECORD_WIRE_DEFAULTS: dict[str, Any] = {
    "fingerprint": None,
    "title": "",
    "company_name": "",
    "recruiter_name": "",
    "recruiter_title": "",
    "is_headhunter": False,
    "company_scale": "",
    "industry": "",
    "tags": [],
    "digest": "",
    "salary_range": "",
    "location": "",
    "job_description": "",
    "status": "unmatched",
    "match_score": None,
    "jd_key_requirements": [],
    "greeting_message": "",
    "greeting_source": "",
    "search_keywords": [],
    "screened_reason": "",
    "screening_stage": "",
    "relaxed_by_whitelist": False,
    "screening_audit": "",
    "applied_at": None,
    "applied_source": "",
    "commute_distance_km": None,
    "commute_distance_text": "",
    "location_line": "",
    "metro_lines": "",
    "metro_station": "",
    "source_task_id": None,
    "first_seen_at": None,
    "last_seen_at": None,
    "created": None,
    "updated": None,
}


def _advanced_status(current: str | None, incoming: Any) -> str | None:
    """The status to write when a new observation meets a stored one, or None to keep it.

    Status only ever moves forward, so a later save-only pass cannot silently demote an
    already-applied job. The two documented exceptions both *lower* the rank on purpose:
    an explicit rejection always wins, and a freshly extracted JD lifts a record out of
    its pre-JD limbo.
    """
    status_val = incoming.value if hasattr(incoming, "value") else incoming
    if not status_val:
        return None
    cur_rank = STATE_RANK.get(current or "", 0)
    new_rank = STATE_RANK.get(status_val, 0)
    if (
        new_rank > cur_rank
        or status_val == JobRecordStatus.IGNORED
        or (
            status_val == JobRecordStatus.JD_SAVED
            and current in (JobRecordStatus.UNMATCHED, JobRecordStatus.DIGEST_ONLY)
        )
    ):
        return status_val
    return None


def _placeholder_fill(existing: dict[str, Any], record_data: dict[str, Any]) -> dict[str, Any]:
    """Fields that fill a gap in the stored record but never overwrite what is known."""
    filled: dict[str, Any] = {}
    for key in (
        "company_scale",
        "industry",
        "recruiter_title",
        "salary_range",
        "location",
        # The station is read from the detail page and a posting's office does not move,
        # so a later observation that finds none must never erase one already recorded.
        "location_line",
        "metro_lines",
        "metro_station",
    ):
        if record_data.get(key) and not existing.get(key):
            filled[key] = record_data[key]
    return filled


def _sticky_field_updates(existing: dict[str, Any], record_data: dict[str, Any]) -> dict[str, Any]:
    """Merge rules for the fields an observation may enrich but never erase.

    One authority for both adapters, so a re-scrape cannot blank what the record already
    holds. The two list fields differ on purpose: ``tags`` are card facets, so the first
    non-empty read wins and a later empty one is ignored, while ``jd_key_requirements`` is
    re-extracted from the JD text and may be refined by a later non-empty read.
    ``is_headhunter`` may be discovered by a later observation but never downgraded — a
    headhunter channel recorded as direct-hire would wrongly join the same-company pool.
    ``greeting_message`` and ``greeting_source`` move together for the same reason: a
    card-level pass that carries neither must leave a human's approved copy and its
    provenance intact (issue #300).
    """
    updates: dict[str, Any] = {}
    # The greeting and who wrote it travel together (issue #300): a pass that brings no
    # greeting of its own must not erase a human's approved copy, and a pass that brings a
    # new one must not leave the old provenance claim standing.
    if record_data.get("greeting_message"):
        updates["greeting_message"] = record_data["greeting_message"]
    if record_data.get("greeting_source"):
        updates["greeting_source"] = record_data["greeting_source"]
    if record_data.get("tags") and not existing.get("tags"):
        updates["tags"] = record_data["tags"]
    if record_data.get("jd_key_requirements"):
        updates["jd_key_requirements"] = record_data["jd_key_requirements"]
    if "is_headhunter" in record_data and (
        record_data["is_headhunter"] or existing.get("is_headhunter") is None
    ):
        updates["is_headhunter"] = record_data["is_headhunter"]
    # Commute distance (spec #209): only overwrite with a known value so
    # a later probe that fails open never erases a measured distance.
    if record_data.get("commute_distance_km") is not None:
        updates["commute_distance_km"] = record_data["commute_distance_km"]
    if record_data.get("commute_distance_text"):
        updates["commute_distance_text"] = record_data["commute_distance_text"]
    return updates


def _record_fields(record_data: dict[str, Any], fingerprint: str, now: str) -> dict[str, Any]:
    """The canonical field set of a brand-new job record, shared by both adapters.

    The field list matches the ``job_records`` Collection Schema, so a key the schema
    does not declare is dropped rather than smuggled onto the wire.
    """
    fields: dict[str, Any] = {
        name: record_data.get(
            name,
            list(default) if isinstance(default, list) else default,
        )
        for name, default in JOB_RECORD_WIRE_DEFAULTS.items()
    }
    fields["fingerprint"] = fingerprint

    status_val = fields.get("status") or JobRecordStatus.UNMATCHED
    if hasattr(status_val, "value"):
        status_val = status_val.value
    fields["status"] = status_val or JobRecordStatus.UNMATCHED.value

    # Boolean and timestamp columns are coerced here because a caller may hand over a
    # value that survived a JSON round-trip (``"false"``) or an ORM-ish truthy object.
    fields["is_headhunter"] = bool(fields.get("is_headhunter", False))
    fields["relaxed_by_whitelist"] = bool(fields.get("relaxed_by_whitelist", False))
    fields["first_seen_at"] = now
    fields["last_seen_at"] = now
    fields["created"] = now
    fields["updated"] = now
    return fields


__all__ = [
    "INVALID_COMPANY_NAMES",
    "JOB_RECORD_WIRE_DEFAULTS",
    "_advanced_status",
    "_placeholder_fill",
    "_record_fields",
    "_sticky_field_updates",
]
