"""
tests/e2e/test_version_resolution_chain.py
==========================================
The join between the shell version probe and the Version Resolution Chain (#478).

`dashboard.sh` sniffs the live checkout's tag and publishes it as `APP_VERSION` /
`PUBLIC_APP_VERSION`, which outrank every other tier in `web/src/lib/server/version.ts`.
`git describe --tags --always` exits **0** with a bare commit hash when no tag is
reachable (an untagged checkout, a shallow clone), so an unguarded sniff exported a raw
hash *above* the Version Manifest — the regression #422 fixed in the TypeScript tier and
this ticket closes in the shell tier.

These tests build real, untagged repositories: the premise (git's output shape) and the
guard are both properties of a real git process, which the fast unit tier forbids. The
resolution declarations that cannot be executed across languages here — the `v0.1` last
resort in the shell, Python and TypeScript — are pinned equal instead, so the three owners
cannot drift apart silently.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts import sync_version

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_SH = REPO_ROOT / "dashboard.sh"
RUNNER_LIB = REPO_ROOT / "runner_lib.sh"
VERSION_TS = REPO_ROOT / "web" / "src" / "lib" / "server" / "version.ts"
BASH = shutil.which("bash") or "/bin/bash"
ENV_BIN = shutil.which("env") or "/usr/bin/env"

#: The shell's shape rule and the TypeScript tier's must agree; compare them on one corpus
#: rather than restating either implementation here.
BARE_HASH = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, *, tag: str | None) -> Path:
    """A real one-commit repository, optionally carrying `tag` on its HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("hello", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    if tag:
        _git(repo, "tag", tag)
    return repo


def _extract_function(name: str) -> str:
    """The function body as written in the real script, not restated here."""
    lines = (REPO_ROOT / "dashboard.sh").read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    collecting = False
    for line in lines:
        if line.startswith(f"{name}() {{"):
            collecting = True
        if collecting:
            body.append(line)
            if line == "}":
                break
    assert body, f"dashboard.sh no longer defines {name}()"
    return "\n".join(body)


def _run_bash(snippet: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            ENV_BIN,
            "-i",
            f"PATH={os.environ.get('PATH', '/usr/bin:/bin')}",
            f"HOME={os.environ.get('HOME', '/tmp')}",
            *[f"{k}={v}" for k, v in env.items()],
            BASH,
            "-c",
            snippet,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _shell_version_env(repo: Path) -> tuple[str, str]:
    """Run `resolve_version_env` with the library sourced, as the runner runs it."""
    harness = "\n".join(
        [
            "set -euo pipefail",
            f'source "{RUNNER_LIB}"',
            _extract_function("resolve_version_env"),
            "resolve_version_env",
            'printf "%s\\n%s\\n" "${APP_VERSION-}" "${PUBLIC_APP_VERSION-}"',
        ]
    )
    result = _run_bash(harness, repo)
    assert result.returncode == 0, result.stderr
    app, public = result.stdout.splitlines()
    return app, public


def test_an_untagged_checkout_never_exports_a_bare_commit_hash(tmp_path: Path) -> None:
    """The reported defect: `--always` answers with a hash, and the shell must refuse it."""
    repo = _repo(tmp_path, tag=None)

    described = subprocess.run(
        ["git", "describe", "--tags", "--always"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert described.returncode == 0, described.stderr
    assert BARE_HASH.match(described.stdout.strip()), (
        "expected git's --always hash fallback; if git changed, the guard's premise did too"
    )

    assert _shell_version_env(repo) == ("", ""), (
        "an untagged checkout exported a version; a bare hash would outrank the manifest"
    )


def test_a_tagged_checkout_still_exports_its_tag(tmp_path: Path) -> None:
    """The control: the guard must not discard a genuine version."""
    repo = _repo(tmp_path, tag="v9.9.9")

    assert _shell_version_env(repo) == ("v9.9.9", "v9.9.9")


def test_the_manifest_producer_rejects_a_hash_shaped_tag(tmp_path: Path) -> None:
    """The Python sniff applies the same shape rule, not only the shell and TypeScript."""
    repo = _repo(tmp_path, tag="abcdef1")
    assert BARE_HASH.match("abcdef1")

    assert sync_version.git_tag_lookup(repo) is None, (
        "a tag shaped like a commit hash was accepted as a version"
    )


def test_an_untagged_checkout_degrades_to_the_shared_fallback(tmp_path: Path) -> None:
    """Untagged: the producer finds no version, and every tier names the same last resort.

    This is the join. The shell has already proved it exports nothing (above), so the
    display resolver skips its env tiers and consults the manifest; the manifest producer,
    given the same untagged checkout, resolves nothing and applies `FALLBACK_VERSION`, which
    is pinned equal to the TypeScript resolver's `DEFAULT_APP_VERSION` and the shell's
    `RUNNER_VERSION_FALLBACK`.
    """
    repo = _repo(tmp_path, tag=None)

    version, source = sync_version.resolve_version_and_source(
        None, tag_lookup=lambda: sync_version.git_tag_lookup(repo)
    )
    assert (version, source) == (None, "git-tag"), (
        "an untagged checkout produced a manifest version instead of falling through"
    )

    version_ts = VERSION_TS.read_text(encoding="utf-8")
    ts_fallback = re.search(r"DEFAULT_APP_VERSION = '([^']+)'", version_ts)
    assert ts_fallback, "version.ts no longer exports DEFAULT_APP_VERSION"
    shell_fallback = re.search(
        r'RUNNER_VERSION_FALLBACK="([^"]+)"', RUNNER_LIB.read_text(encoding="utf-8")
    )
    assert shell_fallback, "runner_lib.sh no longer declares RUNNER_VERSION_FALLBACK"

    assert (
        sync_version.FALLBACK_VERSION == ts_fallback.group(1) == shell_fallback.group(1) == "v0.1"
    ), "the Version Resolution Chain's last resort has diverged across its three owners"

    # The chain's order is the contract the empty export relies on: an unset env tier
    # falls to the sniffed git tag, then the manifest, then the shared fallback.
    assert re.search(
        r"readEnvVersion\('APP_VERSION'\)\s*\?\?\s*readEnvVersion\('PUBLIC_APP_VERSION'\)"
        r"\s*\?\?\s*readGitVersion\(\)\s*\?\?\s*readManifestVersion\(\)\s*\?\?\s*DEFAULT_APP_VERSION",
        version_ts,
    ), "version.ts's resolution order no longer joins the shell tier to the manifest"
