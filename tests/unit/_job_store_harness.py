"""
tests/unit/_job_store_harness.py
================================
Shared PocketBase stand-in for tests that drive a Job Record Store.

Both adapters — in-memory and PocketBase — must agree on everything a Job Record
carries, because the Automation Worker and the Web Dashboard can be pointed at either.
This harness is the seam that proves it: `JobRecordStore` fixtures below hand out a real
adapter of each kind over the same scripted collection endpoint, and the store's own
queries (filter strings, field projections, pagination) run against it for real.

The filter emulator is deliberately strict about PocketBase's quoting rules — a
malformed literal makes the whole query return nothing — because that is how a
company name with a quote in it once silently turned a dedup lookup into a duplicate
insert.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from boss_agent.job_store import PocketBaseJobRecordStore

TODAY = datetime.now(UTC)
YESTERDAY = TODAY - timedelta(days=1)
LONG_AGO = TODAY - timedelta(days=90)


# ---------------------------------------------------------------------------
# Minimal PocketBase collection endpoint, enough to run the store's real queries
# ---------------------------------------------------------------------------


def _split_top(expr: str, operator: str) -> list[str]:
    """Split on a top-level operator, ignoring operator text nested in parentheses."""
    parts, depth, current = [], 0, ""
    i = 0
    while i < len(expr):
        char = expr[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and expr.startswith(operator, i):
            parts.append(current)
            current = ""
            i += len(operator)
            continue
        current += char
        i += 1
    parts.append(current)
    return [p.strip() for p in parts]


def _decode_literal(raw: str) -> str | None:
    """Decode a PocketBase filter literal, or None when its quoting is malformed.

    PocketBase requires a quote inside a quoted value to be backslash-escaped; a raw
    quote is a syntax error that makes the whole query fail. Emulating that here is what
    lets a test prove that an unescaped company name silently breaks the dedup lookup.
    """
    if raw[:1] not in ("'", '"'):
        return raw
    quote = raw[0]
    if raw[-1:] != quote:
        return None
    decoded, i = "", 1
    while i < len(raw) - 1:
        char = raw[i]
        if char == "\\" and i + 1 < len(raw) - 1:
            decoded += raw[i + 1]
            i += 2
            continue
        if char == quote:
            return None
        decoded += char
        i += 1
    return decoded


def _matches(expr: str, item: dict[str, Any]) -> bool:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")") and _split_top(expr[1:-1], "&&"):
        expr = expr[1:-1].strip()

    for operator in ("||", "&&"):
        parts = _split_top(expr, operator)
        if len(parts) > 1:
            results = [_matches(part, item) for part in parts]
            return any(results) if operator == "||" else all(results)

    match = re.match(r"^(\w+)\s*(>=|<=|!=|=|<|>)\s*(.+)$", expr)
    assert match, f"unsupported filter expression: {expr!r}"
    field, op, raw = match.group(1), match.group(2), match.group(3).strip()
    expected = _decode_literal(raw)
    if expected is None:
        # A malformed literal is a syntax error server-side: the query returns nothing.
        return False
    actual = str(item.get(field) or "")
    if op == "=":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == "<":
        return actual < expected
    return actual > expected


class FakePocketBaseSession:
    """Dict-backed stand-in for a PocketBase ``requests.Session``."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self._counter = 0
        # When set, the next patch returns this response instead, so failure paths can be
        # exercised without pretending a write succeeded.
        self.patch_failure: MagicMock | None = None
        # Same, for the insert path: a rejected ``post`` must be scriptable independently.
        self.post_failure: MagicMock | None = None

    def seed(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            self._counter += 1
            record = dict(item)
            record.setdefault("id", f"rec{self._counter}")
            record.setdefault("created", TODAY.isoformat())
            self.records[record["id"]] = record

    def _query(self, params: dict[str, Any]) -> dict[str, Any]:
        items = list(self.records.values())
        if params.get("filter"):
            items = [i for i in items if _matches(params["filter"], i)]
        if params.get("sort", "").lstrip("-") == "created" and params["sort"].startswith("-"):
            items.sort(key=lambda x: str(x.get("created", "")), reverse=True)
        page = int(params.get("page", 1))
        per_page = int(params.get("perPage", 30))
        window = items[(page - 1) * per_page : page * per_page]
        if params.get("fields"):
            wanted = [f.strip() for f in params["fields"].split(",")]
            window = [{k: v for k, v in i.items() if k in wanted} for i in window]
        total_pages = (len(items) + per_page - 1) // per_page if items else 0
        return {"items": window, "totalItems": len(items), "totalPages": total_pages}

    def get(self, url: str, params: dict[str, Any] | None = None, headers=None):
        resp = MagicMock(status_code=200)
        if url.rsplit("/", 1)[-1] in self.records:
            resp.json.return_value = self.records[url.rsplit("/", 1)[-1]]
        else:
            resp.json.return_value = self._query(params or {})
        return resp

    def post(self, url: str, json: dict[str, Any], headers=None):
        if self.post_failure is not None:
            failure, self.post_failure = self.post_failure, None
            return failure
        self._counter += 1
        record = {"id": json.pop("id", None) or f"rec{self._counter}", **json}
        self.records[record["id"]] = record
        return MagicMock(status_code=200, json=MagicMock(return_value=record))

    def patch(self, url: str, json: dict[str, Any], headers=None):
        record_id = url.rsplit("/", 1)[-1]
        if self.patch_failure is not None:
            failure, self.patch_failure = self.patch_failure, None
            return failure
        if record_id not in self.records:
            return MagicMock(status_code=404, text="Not found")
        self.records[record_id].update(json)
        return MagicMock(status_code=200, json=MagicMock(return_value=self.records[record_id]))

    def delete(self, url: str, headers=None):
        self.records.pop(url.rsplit("/", 1)[-1], None)
        return MagicMock(status_code=204)


def pocketbase_job_store(session: FakePocketBaseSession) -> PocketBaseJobRecordStore:
    return PocketBaseJobRecordStore(
        base_url="http://mock-pb:8090",
        session=session,  # type: ignore[arg-type]
        headers=lambda: {"Content-Type": "application/json"},
    )
