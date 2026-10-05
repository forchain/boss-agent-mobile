"""tests/unit/test_generated_types_drift.py
=========================================
Fast-tier unit tests and drift guard for generated dashboard TypeScript types
(Issue #314, Spec #303).

Verifies:
1. Committed web/src/lib/types.generated.ts strictly matches collection_schema.py.
2. Drift guard produces an actionable error message upon discrepancy.
3. All fields from the schema seam (including optional and nullable semantics)
   are accurately represented in the generated TypeScript declaration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.generate_dashboard_types import (
    GENERATED_TYPES_PATH,
    check_generated_types,
    generate_typescript_content,
)

from boss_agent.broker.collection_schema import AUTOMATION_TASKS


def test_generated_dashboard_types_in_sync():
    """Verify that committed web/src/lib/types.generated.ts matches schema generation."""
    in_sync, diff = check_generated_types()
    assert in_sync, (
        f"Generated dashboard types in {GENERATED_TYPES_PATH} have drifted from collection_schema.py!\n"
        f"Diff:\n{diff}\n"
        "Run `uv run python scripts/generate_dashboard_types.py` (or `npm --prefix web run generate-types`) to regenerate."
    )


def test_drift_guard_detects_discrepancy(tmp_path):
    """Verify that any divergence in types.generated.ts is detected with an actionable diff."""
    original_text = GENERATED_TYPES_PATH.read_text(encoding="utf-8")
    mutated_text = original_text.replace("retry_count?: number;", "retry_count?: string;")

    with patch.object(Path, "read_text", return_value=mutated_text):
        in_sync, diff = check_generated_types()
        assert not in_sync
        assert "retry_count" in diff
        assert "-	retry_count?: string;" in diff
        assert "+	retry_count?: number;" in diff


def test_schema_fields_represented_in_generated_task_type():
    """Verify all fields declared on AUTOMATION_TASKS appear in the generated interface."""
    content = generate_typescript_content()
    for field in AUTOMATION_TASKS.fields:
        assert f"{field.name}" in content, (
            f"Field '{field.name}' missing from generated TypeScript interface."
        )

    # Invariants for optional / nullable fields
    assert "worker_id?: string | null;" in content
    assert "locked_at?: string | null;" in content


def test_schema_fields_represented_in_screening_and_saved_search():
    """Verify ScreeningPolicy, SavedSearch, and JobRecord types appear in generated output (Issue #315)."""
    content = generate_typescript_content()

    # ScreeningPolicy
    assert "export interface ScreeningPolicy {" in content
    assert "title_whitelist: string[];" in content
    assert "company_blacklist: string[];" in content
    assert "channel_preference?: 'all' | 'direct_only' | 'headhunter_only';" in content
    assert "max_commute_distance_km?: number | null;" in content

    # JobRecord (unified score representation and applied_at timestamp)
    assert "export interface JobRecord {" in content
    assert "match_score?: number | null;" in content
    assert "applied_at?: string | null;" in content
    assert "status: JobRecordStatus;" in content

    # SavedSearch (includes legacy enable flags with ticket #321 deprecation annotation)
    assert "export interface SavedSearch {" in content
    assert "enable_search?: boolean;" in content
    assert "enable_filter?: boolean;" in content
    assert "filter?: SavedSearchFilter;" in content
    assert "ticket #321" in content
    assert "last_heartbeat_at?: string | null;" in content
    assert "logs: string[];" in content
    assert "payload: Record<string, any>;" in content
