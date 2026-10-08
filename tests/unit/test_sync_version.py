"""tests/unit/test_sync_version.py
=================================
Fast-tier unit tests for the `version.json` producer that backs the
`version.json` consumer tier in `web/src/lib/server/version.ts` (Issue #423).

Verifies:
1. Version formatting matches the CI rule `v<Major>.<Merged_PR_Count>.<commits>`.
2. The persisted manifest carries exactly what the consumer tier reads (`version`).
3. The git-tag fallback fires when no release plan is supplied.
4. Both plan-file-path and raw-JSON-string plan input are accepted.
5. Malformed / colliding inputs degrade instead of raising.

Every git interaction goes through the injectable ``tag_lookup`` seam, so this
file spawns no process -- the fast tier forbids that (see the autouse
``fast_unit_boundary_guard`` in ``tests/unit/conftest.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.sync_version import (
    build_metadata,
    check_manifest,
    format_version,
    load_json_data,
    render_manifest,
    resolve_version,
    write_manifest,
)

# A release plan shaped exactly like the one `.github/scripts/calculate_release.py`
# writes to `.release-tmp/plan.json`, including the tag the CI mechanism will publish.
PLAN: dict[str, Any] = {
    "major": 0,
    "current_pr": {
        "pr_id": 423,
        "pr_count": 98,
        "commits": 1,
        "title": "feat(ci): persist version metadata",
        "author": "octocat",
        "tag_name": "v0.98.1",
        "release_title": "v0.98.1 - feat(ci): persist version metadata",
        "release_body": "## What's Changed",
    },
    "is_collision": False,
}


class TestFormatVersion:
    """The tag format is fixed by `calculate_release.py`; mirror it exactly."""

    def test_matches_the_ci_tag_format(self) -> None:
        assert format_version(0, 98, 1) == "v0.98.1"

    def test_preserves_leading_zero_major(self) -> None:
        assert format_version(0, 1, 1) == "v0.1.1"

    def test_tolerates_major_bump(self) -> None:
        assert format_version(2, 421, 17) == "v2.421.17"

    def test_coerces_string_inputs_from_json(self) -> None:
        # JSON round-trips can hand back strings; the writer must not emit "v0.98.1" wrongly.
        assert format_version("0", "98", "1") == "v0.98.1"

    def test_rejects_nonsense_instead_of_emitting_a_broken_tag(self) -> None:
        with pytest.raises(ValueError):
            format_version("not-a-number", 1, 1)


class TestResolveVersion:
    """Plan wins; git tag is the fallback; neither available degrades sanely."""

    def test_prefers_the_release_plan_tag(self) -> None:
        def lookup() -> str | None:
            raise AssertionError("git must not be consulted when a plan supplies the version")

        assert resolve_version(PLAN, tag_lookup=lookup) == "v0.98.1"

    def test_falls_back_to_the_local_git_tag(self) -> None:
        assert resolve_version(None, tag_lookup=lambda: "v0.42.3") == "v0.42.3"

    def test_falls_back_when_the_plan_carries_no_tag(self) -> None:
        assert resolve_version({"current_pr": {}}, tag_lookup=lambda: "v0.42.3") == "v0.42.3"

    def test_returns_none_when_nothing_resolves(self) -> None:
        assert resolve_version(None, tag_lookup=lambda: None) is None

    def test_survives_a_git_lookup_that_raises(self) -> None:
        # Mirrors `resolve_git_common_root`: a git problem degrades, never breaks the build.
        def boom() -> str | None:
            raise OSError("git not on PATH")

        assert resolve_version(None, tag_lookup=boom) is None

    def test_recomputes_a_missing_tag_from_the_plan_numbers(self) -> None:
        # Defensive: a plan without `tag_name` still carries the three numbers, so the
        # format rule can rebuild it rather than losing the release's version.
        plan = {"current_pr": {"pr_id": 7, "pr_count": 98, "commits": 2}}
        assert resolve_version(plan, tag_lookup=lambda: None) == "v0.98.2"


class TestLoadJsonData:
    """Mirror the tolerant `_load_json_data` pattern from `calculate_release.py`."""

    def test_reads_a_plan_file_path(self, tmp_path: Path) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(PLAN), encoding="utf-8")

        assert load_json_data(str(plan_file)) == PLAN

    def test_reads_a_raw_json_string(self) -> None:
        assert load_json_data(json.dumps(PLAN)) == PLAN

    def test_returns_none_for_garbage(self) -> None:
        assert load_json_data("not json at all") is None

    def test_returns_none_for_an_empty_value(self) -> None:
        assert load_json_data(None) is None
        assert load_json_data("") is None

    def test_returns_none_for_a_missing_file(self, tmp_path: Path) -> None:
        assert load_json_data(str(tmp_path / "absent.json")) is None

    def test_returns_none_for_a_non_object_payload(self) -> None:
        # A JSON array or scalar is not a release plan; treat it as absent.
        assert load_json_data("[1, 2, 3]") is None


class TestMetadata:
    """The persisted shape is the consumer's contract: a string `version` field."""

    def test_carries_the_version_the_consumer_reads(self) -> None:
        metadata = build_metadata(PLAN, source="release-plan")
        assert metadata["version"] == "v0.98.1"
        assert isinstance(metadata["version"], str)

    def test_records_the_plan_provenance(self) -> None:
        metadata = build_metadata(PLAN, source="release-plan")
        assert metadata["tag_name"] == "v0.98.1"
        assert metadata["source"] == "release-plan"
        assert metadata["pr_id"] == 423
        assert metadata["pr_count"] == 98
        assert metadata["commits"] == 1

    def test_git_tag_fallback_leaves_plan_fields_null(self) -> None:
        # Fixed key set either way, so a consumer can rely on the shape.
        metadata = build_metadata(None, version="v0.42.3", source="git-tag")
        assert metadata["version"] == "v0.42.3"
        assert metadata["source"] == "git-tag"
        assert metadata["pr_id"] is None
        assert metadata["pr_count"] is None
        assert metadata["commits"] is None

    def test_extra_fields_are_harmless_for_the_consumer(self) -> None:
        # `version.ts` ignores unknown keys; pin that our own reader agrees.
        rendered = render_manifest(build_metadata(PLAN, source="release-plan"))
        assert json.loads(rendered)["version"] == "v0.98.1"


class TestWriteAndCheck:
    """Disk behaviour, exercised through tmp_path -- never the repo root."""

    def test_writes_a_manifest_the_consumer_can_parse(self, tmp_path: Path) -> None:
        target = tmp_path / "version.json"
        metadata = build_metadata(PLAN, source="release-plan")

        write_manifest(target, metadata)

        parsed = json.loads(target.read_text(encoding="utf-8"))
        assert parsed["version"] == "v0.98.1"

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "dir" / "version.json"
        write_manifest(target, build_metadata(PLAN, source="release-plan"))
        assert target.exists()

    def test_written_manifest_is_stable_across_runs(self, tmp_path: Path) -> None:
        # No timestamp field: `--check` in CI must be able to compare byte-for-byte.
        target = tmp_path / "version.json"
        write_manifest(target, build_metadata(PLAN, source="release-plan"))
        first = target.read_text(encoding="utf-8")
        write_manifest(target, build_metadata(PLAN, source="release-plan"))
        assert target.read_text(encoding="utf-8") == first

    def test_check_passes_for_a_matching_manifest(self, tmp_path: Path) -> None:
        target = tmp_path / "version.json"
        metadata = build_metadata(PLAN, source="release-plan")
        write_manifest(target, metadata)

        assert check_manifest(target, metadata) is True

    def test_check_reports_drift_when_the_version_differs(self, tmp_path: Path) -> None:
        target = tmp_path / "version.json"
        target.write_text(json.dumps({"version": "v0.1.1"}), encoding="utf-8")

        assert check_manifest(target, build_metadata(PLAN, source="release-plan")) is False

    def test_check_reports_drift_when_the_manifest_is_absent(self, tmp_path: Path) -> None:
        target = tmp_path / "version.json"
        assert check_manifest(target, build_metadata(PLAN, source="release-plan")) is False

    def test_check_does_not_raise_on_a_malformed_manifest(self, tmp_path: Path) -> None:
        target = tmp_path / "version.json"
        target.write_text("not json at all", encoding="utf-8")

        assert check_manifest(target, build_metadata(PLAN, source="release-plan")) is False


class TestManifestMatchesConsumerContract:
    """Guard the exact shape `web/src/lib/server/version.ts` reads."""

    def test_version_key_is_the_only_required_field(self) -> None:
        rendered = render_manifest(build_metadata(PLAN, source="release-plan"))
        parsed = json.loads(rendered)

        assert isinstance(parsed, dict)
        assert isinstance(parsed["version"], str)
        assert parsed["version"].strip() == "v0.98.1"
