#!/usr/bin/env python3
"""scripts/sync_version.py
=======================
Generate and persist the `version.json` manifest that the Web Dashboard's version
resolver reads (Issue #423).

`web/src/lib/server/version.ts` resolves the displayed version as:
`APP_VERSION` -> `PUBLIC_APP_VERSION` -> git tag -> `version.json` manifest -> `v0.1`.
The manifest tier is what lets a built artifact with no `.git` directory -- and
therefore no tag to describe -- still report the version it was released under.
This script is the producer for that tier.

Source of truth:
    1. A release plan produced by `.github/scripts/calculate_release.py`
       (a file path or a raw JSON string), or
    2. The local git tag, when no plan is supplied.

Version format is fixed by the CI mechanism:
    `v<Major>.<Merged_PR_Count>.<commits>`
where Major comes from the repo-root `VERSION` file, Minor is the sequential merged
PR count, and Patch is the commit count in the PR.

Usage:
    uv run python scripts/sync_version.py --plan .release-tmp/plan.json
    uv run python scripts/sync_version.py --plan '{"current_pr": {"tag_name": "v0.98.1"}}'
    uv run python scripts/sync_version.py            # fall back to the local git tag
    uv run python scripts/sync_version.py --check    # verify without writing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "version.json"

# Last resort matching the consumer's own `DEFAULT_APP_VERSION` in version.ts, so a
# manifest that somehow lands here is never a *worse* answer than the tier below it.
FALLBACK_VERSION = "v0.1"


def format_version(major: Any, pr_count: Any, commits: Any) -> str:
    """Format a version as `v<Major>.<Merged_PR_Count>.<commits>`.

    Mirrors the tag built at `.github/scripts/calculate_release.py:92`. Values arrive
    from JSON and may be strings, so each is coerced; anything that is not an integer
    raises rather than silently emitting a malformed tag like `vNone.1.1`.
    """
    parts = []
    for value in (major, pr_count, commits):
        try:
            parts.append(int(str(value).strip()))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"version component is not an integer: {value!r}") from exc
    return f"v{parts[0]}.{parts[1]}.{parts[2]}"


def load_json_data(file_or_raw: str | None) -> Any | None:
    """Load JSON from a file path if it exists, else parse the value as raw JSON.

    Mirrors the tolerant `_load_json_data` pattern in `.github/scripts/calculate_release.py`:
    every failure mode returns `None` ("no plan available") instead of raising, because a
    missing or malformed plan must degrade to the git-tag tier rather than break the build.
    """
    if not file_or_raw:
        return None

    # A release plan is a JSON object. Anything else (array, scalar) is not a plan.
    def _as_object(value: Any) -> Any | None:
        return value if isinstance(value, dict) else None

    path = Path(file_or_raw)
    try:
        # `exists()` itself can raise `OSError` (ENAMETOOLONG) for a long raw JSON
        # string, which is a legitimate input here -- so it is inside its own guard.
        # A False result is *not* a failure: it just means the value is raw JSON.
        is_file = path.exists()
    except Exception:
        is_file = False

    if is_file:
        try:
            return _as_object(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None
    try:
        return _as_object(json.loads(file_or_raw))
    except Exception:
        return None


def version_from_plan(plan: Any) -> str | None:
    """Extract the release version from a release plan, or `None` if it carries none."""
    if not isinstance(plan, dict):
        return None

    current = plan.get("current_pr")
    if isinstance(current, dict):
        tag = current.get("tag_name")
        if isinstance(tag, str) and tag.strip():
            return tag.strip()

    # Defensive: a plan missing `tag_name` still carries the three numbers the format
    # rule needs, so rebuild rather than losing the release's version entirely.
    if isinstance(current, dict) and "pr_count" in current and "commits" in current:
        try:
            return format_version(plan.get("major", 0), current["pr_count"], current["commits"])
        except ValueError:
            return None
    return None


def git_tag_lookup(cwd: str | Path | None = None) -> str | None:
    """Return the nearest local git tag, or `None` when git cannot answer.

    This is the one and only place this module touches a subprocess, and it is always
    called through the injectable ``tag_lookup`` seam -- the fast unit tier forbids
    spawning processes, and a real-git check belongs in `tests/e2e`. Degradation follows
    `resolve_git_common_root` in `src/boss_agent/settings.py`: a bare `except Exception`
    returning `None` rather than propagating, so a detached checkout with no `.git` or no
    git binary still produces a usable manifest.
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(cwd) if cwd else str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        tag = result.stdout.strip()
        return tag or None
    except Exception:
        return None


def resolve_version(plan: Any, tag_lookup: Any = git_tag_lookup) -> str | None:
    """Resolve the version for a manifest: the release plan first, the git tag second.

    ``tag_lookup`` is injected rather than called directly so unit tests can exercise
    this logic without spawning a process; it is called only when the plan yields nothing.
    """
    version = version_from_plan(plan)
    if version:
        return version

    try:
        tag = tag_lookup()
    except Exception:
        # A git problem must not fail the build; the caller falls back to FALLBACK_VERSION.
        return None
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    return None


def build_metadata(
    plan: Any,
    version: str | None = None,
    source: str = "release-plan",
) -> dict[str, Any]:
    """Build the `version.json` payload.

    The shape is fixed by the consumer: `web/src/lib/server/version.ts` reads a single
    string `version` field and ignores everything else. The extra keys are provenance for
    humans and for `--check`, and the key set is identical on every path so a consumer can
    rely on it. No timestamp: a stable payload is what lets `--check` compare exactly.
    """
    current = plan.get("current_pr") if isinstance(plan, dict) else None
    current = current if isinstance(current, dict) else {}

    resolved = version or resolve_version(plan, tag_lookup=lambda: None) or FALLBACK_VERSION
    return {
        "version": resolved,
        "tag_name": current.get("tag_name") or resolved,
        "source": source,
        "pr_id": current.get("pr_id"),
        "pr_count": current.get("pr_count"),
        "commits": current.get("commits"),
    }


def render_manifest(metadata: dict[str, Any]) -> str:
    """Serialise a manifest payload to the exact text written to disk."""
    return json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"


def write_manifest(path: Path, metadata: dict[str, Any]) -> None:
    """Write the manifest to `path`, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest(metadata), encoding="utf-8")


def check_manifest(path: Path, metadata: dict[str, Any]) -> bool:
    """Report whether the manifest on disk already matches `metadata`.

    A missing, unreadable or malformed manifest is drift, not an error: the caller turns
    a `False` into a clear "run without --check" message rather than a traceback.
    """
    try:
        if not path.exists():
            return False
        return path.read_text(encoding="utf-8") == render_manifest(metadata)
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the version.json manifest.")
    parser.add_argument(
        "--plan",
        help="Release plan JSON file path or raw JSON string (falls back to the local git tag)",
    )
    parser.add_argument(
        "--output",
        help=f"Manifest output path (default: {MANIFEST_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the existing manifest matches, without writing",
    )
    args = parser.parse_args()

    output = Path(args.output) if args.output else MANIFEST_PATH
    plan = load_json_data(args.plan)
    source = "release-plan" if version_from_plan(plan) else "git-tag"
    version = resolve_version(plan)

    if version is None:
        print(
            f"Warning: no release plan and no local git tag; falling back to {FALLBACK_VERSION}.",
            file=sys.stderr,
        )

    metadata = build_metadata(plan, version=version, source=source)

    if args.check:
        if check_manifest(output, metadata):
            print(f"✓ {output} is up to date ({metadata['version']}).")
            return 0
        print(
            f"ERROR: {output} does not match the resolved version {metadata['version']}.",
            file=sys.stderr,
        )
        print("\nRun `uv run python scripts/sync_version.py` to regenerate.", file=sys.stderr)
        return 1

    write_manifest(output, metadata)
    try:
        shown = output.relative_to(REPO_ROOT)
    except ValueError:
        shown = output
    print(f"✓ Version manifest written to {shown} ({metadata['version']}, via {source}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
