"""
tests/e2e/test_sync_version_cli.py
==================================
End-to-end tests for scripts/sync_version.py, the producer behind the `version.json`
tier of `web/src/lib/server/version.ts` (Issue #423).

These live in the E2E tier rather than `tests/unit/` because they spawn a real `git`
through the module's `git_tag_lookup` seam and a real interpreter via the CLI. The fast
tier forbids subprocess execution outright (see the autouse
`fast_unit_boundary_guard` in `tests/unit/conftest.py`), and the resolution *policy* is
already covered there against an injected seam -- what is left to prove here is that the
real seam and the real command line behave as the policy assumes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.sync_version import git_tag_lookup

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_version.py"


def test_git_tag_lookup_reads_a_real_tag(tmp_path: Path) -> None:
    """A real repository with a real tag resolves through the unstubbed git seam."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (repo / "file.txt").write_text("hello", encoding="utf-8")
    run("add", "file.txt")
    run("commit", "-q", "-m", "initial")
    run("tag", "v9.9.9")

    assert git_tag_lookup(cwd=repo) == "v9.9.9"


def test_git_tag_lookup_degrades_in_a_directory_without_git(tmp_path: Path) -> None:
    """No `.git`, no `git` binary, no tag: the seam returns None instead of raising.

    This is the shape of a built artifact, and it is why `git_tag_lookup` swallows every
    failure rather than propagating it -- the caller must still produce a manifest.
    """
    detached = tmp_path / "detached"
    detached.mkdir()

    assert git_tag_lookup(cwd=detached) is None


def test_cli_writes_a_manifest_a_git_detached_consumer_can_read(tmp_path: Path) -> None:
    """The real CLI output must carry the `version` string `version.ts` reads.

    This is the end-to-end contract of #423: whatever CI writes here is the only version
    signal a `.git`-free built artifact has, so the field name and type are pinned
    against a file produced by an actual process, not by a direct function call.
    """
    output = tmp_path / "version.json"
    plan = {
        "major": 0,
        "current_pr": {"pr_id": 423, "pr_count": 98, "commits": 1, "tag_name": "v0.98.1"},
    }

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--plan",
            json.dumps(plan),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(parsed["version"], str)
    assert parsed["version"] == "v0.98.1"


def test_cli_check_detects_drift_without_writing(tmp_path: Path) -> None:
    """`--check` fails on a mismatched manifest and succeeds once regenerated."""
    output = tmp_path / "version.json"
    plan = {
        "major": 0,
        "current_pr": {"pr_id": 1, "pr_count": 98, "commits": 1, "tag_name": "v0.98.1"},
    }

    def run_check() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--plan",
                json.dumps(plan),
                "--output",
                str(output),
                "--check",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    # Absent manifest is drift.
    assert run_check().returncode == 1

    # Regenerate, then the check passes.
    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--plan", json.dumps(plan), "--output", str(output)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert run_check().returncode == 0

    # A different release version is drift again.
    other_plan = {
        "major": 0,
        "current_pr": {"pr_id": 2, "pr_count": 99, "commits": 3, "tag_name": "v0.99.3"},
    }
    drifted = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--plan",
            json.dumps(other_plan),
            "--output",
            str(output),
            "--check",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drifted.returncode == 1


def test_cli_survives_a_malformed_plan_and_falls_back_to_the_git_tag(tmp_path: Path) -> None:
    """Garbage plan input must degrade to the git tag tier, not crash the release."""
    output = tmp_path / "version.json"
    repo_tag = git_tag_lookup(cwd=REPO_ROOT)
    assert repo_tag, "expected the repository checkout to have a reachable tag"

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--plan", "not json at all", "--output", str(output)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["version"] == repo_tag
    assert parsed["source"] == "git-tag"
