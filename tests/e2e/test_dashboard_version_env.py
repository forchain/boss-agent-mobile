"""
tests/e2e/test_dashboard_version_env.py
========================================
The version-injection block of `dashboard.sh` (#421).

`cmd_start` decides which version string reaches the Web Dashboard through two
environment variables, `APP_VERSION` and `PUBLIC_APP_VERSION`. The second carries the
`PUBLIC_` prefix on purpose: SvelteKit only exposes `PUBLIC_*` variables to the client
bundle, so `web/src/lib/server/version.ts` needs it to render the navbar badge.

That block used to end with `export PUBLIC_APP_VERSION="${APP_VERSION}"`, an
unconditional assignment. An operator who exported only `PUBLIC_APP_VERSION=v9.9.9`
therefore silently lost their value to whatever `git describe` sniffed from the
checkout. These tests pin the corrected rule: each variable falls through to the next
tier only when it is *unset*, never when it is set.

Why the E2E tier: the block is bash, so proving it needs a real `bash` process and a
real `git` in a real repository. `tests/unit/conftest.py` rejects every subprocess
outright. Only `resolve_version_env()` is extracted and invoked -- `cmd_start` itself
never runs, so this suite starts no service and binds no port.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_SH = REPO_ROOT / "dashboard.sh"
BASH = shutil.which("bash") or "/bin/bash"
ENV_BIN = shutil.which("env") or "/usr/bin/env"

pytestmark = pytest.mark.e2e

# The tag the throwaway repository below is tagged with, and the versions an operator
# would pin by hand. Every value is distinct from every other on purpose: a test whose
# expected value happened to equal what a *sniff* would have produced would pass even
# with the defect back in place, proving nothing about precedence.
REPO_TAG = "v9.9.9"
OPERATOR_PUBLIC = "v7.7.7"
OPERATOR_APP = "v8.8.8"


def _tagged_repo(tmp_path: Path) -> Path:
    """A real git repository whose HEAD carries ``REPO_TAG``."""
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
    run("tag", REPO_TAG)
    return repo


def _resolve_version_env(cwd: Path, env_pairs: list[str]) -> tuple[str, str]:
    """Run `resolve_version_env()` from `dashboard.sh` in `cwd`; return both variables.

    The function body is read out of the real script rather than restated here, so
    editing `dashboard.sh` moves this test with it instead of letting the two drift.
    Only `resolve_version_env()` is extracted and run -- `cmd_start` itself is never
    invoked, so this suite starts no service and binds no port.
    """
    lines = DASHBOARD_SH.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    collecting = False
    for line in lines:
        if line.startswith("resolve_version_env() {"):
            collecting = True
        if collecting:
            body.append(line)
            if line == "}":
                break
    assert body, (
        "dashboard.sh no longer defines resolve_version_env(); the version-injection "
        "block must stay extractable so tests/e2e can cover it"
    )

    harness = "\n".join(
        [
            # Same strictness as the real script, so a regression that trips `set -u`
            # or `set -e` fails here exactly as it would on a developer's machine.
            "set -euo pipefail",
            *body,
            "resolve_version_env",
            'printf "%s\\n%s\\n" "${APP_VERSION-}" "${PUBLIC_APP_VERSION-}"',
        ]
    )

    result = subprocess.run(
        [
            ENV_BIN,
            "-i",
            # `env -i` starts from an empty environment, so an APP_VERSION exported in
            # the developer's shell cannot make an "unset" case pass by accident.
            f"PATH={os.environ.get('PATH', '/usr/bin:/bin')}",
            # git only needs a HOME to read global config; the throwaway repos
            # already carry their own local config from `_tagged_repo`.
            f"HOME={os.environ.get('HOME', '/tmp')}",
            *env_pairs,
            BASH,
            "-c",
            harness,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"resolve_version_env() exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    reported = result.stdout.splitlines()
    assert len(reported) == 2, f"expected two variables, got {result.stdout!r}"
    return reported[0], reported[1]


def test_operator_set_public_app_version_survives_the_git_sniff(tmp_path: Path) -> None:
    """`PUBLIC_APP_VERSION` alone must win even though the checkout has a tag.

    This is the reported defect: the old unconditional `PUBLIC_APP_VERSION="${APP_VERSION}"`
    overwrote the operator's value with the sniffed tag. `APP_VERSION` is still free to
    sniff -- it was not pinned -- so the two are asserted separately, which is only
    meaningful because the operator value differs from the tag.
    """
    app, public = _resolve_version_env(
        _tagged_repo(tmp_path), [f"PUBLIC_APP_VERSION={OPERATOR_PUBLIC}"]
    )

    assert (app, public) == (REPO_TAG, OPERATOR_PUBLIC)


def test_operator_set_app_version_reaches_both_variables(tmp_path: Path) -> None:
    """`APP_VERSION` alone seeds both, so the public tier is never left empty."""
    app, public = _resolve_version_env(_tagged_repo(tmp_path), [f"APP_VERSION={OPERATOR_APP}"])

    assert (app, public) == (OPERATOR_APP, OPERATOR_APP)


def test_explicitly_set_variables_are_each_preserved(tmp_path: Path) -> None:
    """With both set they are independent knobs; neither overwrites the other."""
    app, public = _resolve_version_env(
        _tagged_repo(tmp_path),
        [f"APP_VERSION={OPERATOR_APP}", f"PUBLIC_APP_VERSION={OPERATOR_PUBLIC}"],
    )

    assert (app, public) == (OPERATOR_APP, OPERATOR_PUBLIC)


def test_unset_variables_fall_back_to_the_sniffed_git_tag(tmp_path: Path) -> None:
    """Neither set: the live checkout's tag is the answer, for both variables."""
    app, public = _resolve_version_env(_tagged_repo(tmp_path), [])

    assert (app, public) == (REPO_TAG, REPO_TAG)


def test_unset_variables_outside_a_repository_degrade_to_empty(tmp_path: Path) -> None:
    """No repo and no git output: both variables are empty and the script still exits 0.

    The empty result is deliberate -- the server resolver then falls through to
    `version.json` and the `v0.1` preset -- but it must never abort the dashboard
    under `set -euo pipefail`.
    """
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    app, public = _resolve_version_env(plain, [])

    assert (app, public) == ("", "")


def test_operator_value_survives_a_failed_sniff(tmp_path: Path) -> None:
    """A degraded `git describe` must not blank out an operator-set version.

    The failure and the override arrive together in the field: a tag that no longer
    exists, or a `.git`-free artifact, on a machine where the operator pinned the
    version by hand.

    `APP_VERSION` stays empty here, and that is correct rather than a gap: it was never
    set and there was nothing to sniff. The precedence is APP -> PUBLIC, so PUBLIC never
    back-fills APP -- what matters is that the pinned value survives untouched.
    """
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    app, public = _resolve_version_env(plain, [f"PUBLIC_APP_VERSION={OPERATOR_PUBLIC}"])

    assert (app, public) == ("", OPERATOR_PUBLIC)
