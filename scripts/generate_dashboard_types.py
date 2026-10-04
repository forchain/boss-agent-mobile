"""scripts/generate_dashboard_types.py
=====================================
Generates TypeScript declarations for the Web Dashboard from the authoritative
PocketBase collection schema seam and domain entities (Issues #314, #315, Spec #303).

Usage:
    uv run python scripts/generate_dashboard_types.py
    uv run python scripts/generate_dashboard_types.py --check
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED_TYPES_PATH = REPO_ROOT / "web" / "src" / "lib" / "types.generated.ts"

KIND_TO_TS = {
    "text": "string",
    "number": "number",
    "bool": "boolean",
    "json": "Record<string, any>",
    "date": "string",
    "autodate": "string",
}


def _generate_collection_interface(collection, interface_name: str) -> list[str]:
    lines = [f"export interface {interface_name} {{"]
    for field in collection.fields:
        ts_type = field.ts_type or KIND_TO_TS.get(field.kind, "string")
        if field.optional is not None:
            is_optional = field.optional
        else:
            is_optional = not field.required and not field.primary_key
        opt_marker = "?" if is_optional else ""
        if field.description:
            lines.append(f"\t/** {field.description} */")
        lines.append(f"\t{field.name}{opt_marker}: {ts_type};")
    lines.append("}")
    return lines


def generate_typescript_content() -> str:
    from boss_agent.broker.collection_schema import (
        AUTOMATION_TASKS,
        JOB_RECORDS,
        SAVED_SEARCHES,
    )

    lines: list[str] = [
        "/**",
        " * AUTO-GENERATED FILE — DO NOT EDIT DIRECTLY.",
        " *",
        " * Generated from collection_schema.py and domain entities by scripts/generate_dashboard_types.py.",
        " * Run `npm run generate-types` or `uv run python scripts/generate_dashboard_types.py` to regenerate.",
        " */",
        "",
        "// ---------------------------------------------------------------------------",
        "// Automation Tasks (Issue #314)",
        "// ---------------------------------------------------------------------------",
        "",
        "export type TaskStatus =",
        "\t| 'pending'",
        "\t| 'running'",
        "\t| 'paused_for_takeover'",
        "\t| 'resuming'",
        "\t| 'success'",
        "\t| 'failed'",
        "\t| 'cancelled';",
        "",
        "export type TaskType = 'AUTO_APPLY' | 'SCRAPE_JOBS' | 'CHECK_LOGIN' | 'CHECK_CHAT';",
        "",
        "/** The task types the worker's handler strategy accepts, in one place. */",
        "export const TASK_TYPES: readonly TaskType[] = [",
        "\t'AUTO_APPLY',",
        "\t'SCRAPE_JOBS',",
        "\t'CHECK_LOGIN',",
        "\t'CHECK_CHAT'",
        "];",
        "",
    ]

    lines.extend(_generate_collection_interface(AUTOMATION_TASKS, "AutomationTask"))
    lines.extend(
        [
            "",
            "// ---------------------------------------------------------------------------",
            "// Screening Policy (Issue #315)",
            "// ---------------------------------------------------------------------------",
            "",
            "export interface ScreeningPolicy {",
            "\ttitle_whitelist: string[];",
            "\ttitle_blacklist: string[];",
            "\tcompany_blacklist: string[];",
            "\tjd_blacklist: string[];",
            "\tbusiness_district_blacklist: string[];",
            "\t/**",
            "\t * Borderline districts whose direct-hire postings are worth measuring against the",
            "\t * commute ceiling (spec #328). Empty means nothing is probed.",
            "\t */",
            "\tbusiness_district_inspect_list: string[];",
            "\tenable_screening: boolean;",
            "\tchannel_preference?: 'all' | 'direct_only' | 'headhunter_only';",
            "\t/** Commute ceiling in km; null, blank or <= 0 disables distance filtering. */",
            "\tmax_commute_distance_km?: number | null;",
            "}",
            "",
            "// ---------------------------------------------------------------------------",
            "// Job Records (Issue #315: unified score and applied-timestamp representation)",
            "// ---------------------------------------------------------------------------",
            "",
            "export type TargetAction = 'save_jd' | 'auto_apply';",
            "",
            "export type JobRecordStatus =",
            "\t| 'jd_saved'",
            "\t| 'unmatched'",
            "\t| 'matched'",
            "\t| 'applied'",
            "\t| 'ignored'",
            "\t| 'digest_only';",
            "",
        ]
    )
    lines.extend(_generate_collection_interface(JOB_RECORDS, "JobRecord"))
    lines.extend(
        [
            "",
            "// ---------------------------------------------------------------------------",
            "// Saved Searches & Filters (Issue #315)",
            "// ---------------------------------------------------------------------------",
            "",
            "/**",
            " * Recruitment channel a strategy targets (issue #368).",
            " *",
            " * `''` is a real fourth state, not a missing key: it means \"inherit the system-wide",
            ' * `ScreeningPolicy` setting", which is what every preset written before channel',
            " * filtering existed has always done. `all` is the deliberate widening override, and it",
            " * is distinct from `''` precisely so a strategy can say \"search both channels\" over a",
            " * global setting of `direct_only`.",
            " *",
            " * A `ScreeningPolicy` never takes this union: it carries the *effective* channel, so it",
            " * has no `''` to inherit from.",
            " */",
            "export type ChannelPreference = '' | 'all' | 'direct_only' | 'headhunter_only';",
            "",
            "export interface SavedSearchFilter {",
            "\teducation?: string;",
            "\tsalary?: string;",
            "\texperience?: string;",
            "\tactivity?: string;",
            "\tcompany_scales?: string[];",
            "\tindustries?: string[];",
            "\tchannel_preference?: ChannelPreference;",
            "}",
            "",
        ]
    )
    lines.extend(_generate_collection_interface(SAVED_SEARCHES, "SavedSearch"))
    lines.append("")
    return "\n".join(lines)


def check_generated_types() -> tuple[bool, str]:
    expected = generate_typescript_content()
    if not GENERATED_TYPES_PATH.exists():
        return False, f"File does not exist: {GENERATED_TYPES_PATH}"
    actual = GENERATED_TYPES_PATH.read_text(encoding="utf-8")
    if actual == expected:
        return True, ""
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(GENERATED_TYPES_PATH),
            tofile="generated",
        )
    )
    return False, diff


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate dashboard TypeScript declarations")
    parser.add_argument("--check", action="store_true", help="Check for drift without writing")
    args = parser.parse_args()

    if args.check:
        in_sync, diff = check_generated_types()
        if not in_sync:
            print(
                "ERROR: Generated dashboard types are out of sync with collection schema!",
                file=sys.stderr,
            )
            print(diff, file=sys.stderr)
            print(
                "\nRun `uv run python scripts/generate_dashboard_types.py` to regenerate.",
                file=sys.stderr,
            )
            return 1
        print("✓ Generated dashboard types are in sync with collection schema.")
        return 0

    content = generate_typescript_content()
    GENERATED_TYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_TYPES_PATH.write_text(content, encoding="utf-8")
    print(f"✓ Generated dashboard types written to {GENERATED_TYPES_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
