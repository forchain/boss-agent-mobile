"""
tests/e2e/test_git_describe_shallow_clone.py
===========================================
The real-git premise behind `web/src/lib/server/gitDescribe.ts`'s hash degradation (#422).

`git describe --tags --always` was assumed to fail when no tag is reachable. It does
not: `--always` makes git fall back to the abbreviated commit hash and exit **0**. In a
shallow clone -- `git clone --depth 1`, the default shape of a CI checkout artifact --
no tag was ever fetched, so the hash is all `describe` can offer, and code that trusts
the exit status ships a bare commit id to the navbar as if it were a version.

`node:child_process` is mocked in the vitest suite, so it proves the TypeScript rule
against a hand-written string. This suite proves the *input* is real: it builds a real
repository, clones it shallowly, and pins what git actually prints and returns. If a
future git changed that contract, the vitest expectations would still pass while the
degradation silently stopped being needed -- or, worse, stopped being correct.

E2E tier rather than unit: it spawns real `git`, which the fast tier forbids outright
(see the autouse ``fast_unit_boundary_guard`` in ``tests/unit/conftest.py``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# The shape `runGitDescribe` treats as "no version available": hex digits only, at git's
# abbreviation length. Kept here as the assertion, not as the implementation -- the
# rule under test lives in `web/src/lib/server/gitDescribe.ts`.
BARE_HASH = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)

# git's minimum abbreviation is 7 hex characters, upper bound is a sha1 object id.
MIN_ABBREV = 7
MAX_ABBREV = 40


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def tagged_origin(tmp_path: Path) -> Path:
    """A repository with two commits, the tag pinned to the *first* of them.

    HEAD sitting one commit past the tag is what makes a `--depth 1` clone tagless:
    git fetches tags that point at the commits it actually fetched, so a clone of a
    tagged HEAD would carry the tag and would not reproduce the field condition at all.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "test@example.com")
    _git(origin, "config", "user.name", "Test")
    (origin / "file.txt").write_text("hello", encoding="utf-8")
    _git(origin, "add", "file.txt")
    _git(origin, "commit", "-q", "-m", "initial")
    _git(origin, "tag", "v9.9.9")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "second")
    return origin


def test_a_shallow_clone_reports_a_bare_hash_as_success(
    tagged_origin: Path, tmp_path: Path
) -> None:
    """`git describe --tags --always` exits 0 in a shallow clone and prints a raw hash.

    This is the reproduced failure: exit status cannot distinguish "no version here"
    from "here is your version", so the caller has to inspect the output shape.
    """
    shallow = tmp_path / "shallow"
    # `file://` is required: git silently ignores --depth for a plain local path.
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{tagged_origin}", str(shallow)],
        check=True,
        capture_output=True,
        text=True,
    )

    # The shallow clone really has no tags at all -- this is what makes the case work.
    assert _git(shallow, "tag", "--list") == ""

    describe = subprocess.run(
        ["git", "describe", "--tags", "--always"],
        cwd=shallow,
        capture_output=True,
        text=True,
        check=False,
    )

    assert describe.returncode == 0, f"expected success, got: {describe.stderr}"
    assert BARE_HASH.match(describe.stdout.strip()), (
        "expected the --always hash fallback; git's behaviour may have changed"
    )


def test_the_full_clone_by_contrast_still_reports_the_tag(tagged_origin: Path) -> None:
    """The control: with the tag present, `describe` returns a real version string.

    A full clone fetches the tag, so it describes cleanly against the first commit.
    Without this control, a git that returned a hash for *every* clone would still
    satisfy the case above, and the degradation would have been over-broad.
    """
    full = tagged_origin.parent / "full"
    subprocess.run(
        ["git", "clone", "-q", f"file://{tagged_origin}", str(full)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert _git(full, "tag", "--list") == "v9.9.9"
    described = subprocess.run(
        ["git", "describe", "--tags", "--always"],
        cwd=full,
        capture_output=True,
        text=True,
        check=False,
    )

    assert described.returncode == 0, described.stderr
    # The clone is on the second commit, one past the tag: a real version string, not
    # a bare hash. This is the shape `runGitDescribe` must let through untouched.
    assert described.stdout.strip().startswith("v9.9.9-1-g")


def test_a_commit_past_the_tag_is_not_a_bare_hash(tagged_origin: Path) -> None:
    """Commits after the tag produce `v9.9.9-1-g<hash>`, which must never be discarded.

    The hash suffix is the trap in the other direction: output that *contains* hex is
    still a version, and treating it as a leaked commit id would silently downgrade
    every ordinary dev build to the fallback.
    """
    full = tagged_origin.parent / "past"
    subprocess.run(
        ["git", "clone", "-q", f"file://{tagged_origin}", str(full)],
        check=True,
        capture_output=True,
        text=True,
    )

    described = _git(full, "describe", "--tags", "--always")

    assert described.startswith("v9.9.9")
    assert not BARE_HASH.match(described)
    assert MIN_ABBREV <= len(described.split("-g")[-1]) <= MAX_ABBREV
