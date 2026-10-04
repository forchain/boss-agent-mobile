"""
tests/unit/test_pb_runner_lifecycle.py
======================================
Integration tests verifying PocketBase pre-provisioning, graceful shutdown,
and database persistence across server restarts.
"""

import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from boss_agent.broker.provisioner import provision_sqlite_database
from boss_agent.settings import resolve_git_common_root


@pytest.fixture
def pb_bin():
    binary = shutil.which("pocketbase")
    if not binary:
        pytest.skip("PocketBase binary not installed on system")
    return binary


def test_pocketbase_preprovision_and_clean_boot(tmp_path: Path, pb_bin: str):
    """Verify that offline migrate up + provisioner allows PocketBase to boot with all collections recognized."""
    pb_dir = tmp_path / "pb_data"
    pb_dir.mkdir(parents=True, exist_ok=True)
    db_file = pb_dir / "data.db"

    # 1. Run pocketbase migrate up offline
    migrate_res = subprocess.run(
        [pb_bin, "migrate", "up", "--dir", str(pb_dir)],
        capture_output=True,
        text=True,
    )
    assert migrate_res.returncode == 0
    assert db_file.exists()

    # 2. Pre-provision schema
    assert provision_sqlite_database(db_file) is True

    # 3. Create superuser
    su_res = subprocess.run(
        [pb_bin, "superuser", "upsert", "admin@test.local", "securepass123", "--dir", str(pb_dir)],
        capture_output=True,
        text=True,
    )
    assert su_res.returncode == 0

    # 4. Start PocketBase server
    test_port = "8977"
    proc = subprocess.Popen(
        [pb_bin, "serve", "--dir", str(pb_dir), "--http", f"127.0.0.1:{test_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for health check
        healthy = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/health", timeout=1.0) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy, "PocketBase failed to become healthy within 3s"

        # 5. Check that saved_searches is immediately accessible with HTTP 200 (not 404!)
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/collections/saved_searches/records") as resp:
            assert resp.status == 200
            data = resp.read().decode("utf-8")
            assert "default_agent_search" in data

    finally:
        # 6. Graceful shutdown
        proc.terminate()
        proc.wait(timeout=5)

    # 7. Start PocketBase AGAIN (simulate restart) and verify superuser & saved searches persist
    proc2 = subprocess.Popen(
        [pb_bin, "serve", "--dir", str(pb_dir), "--http", f"127.0.0.1:{test_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        healthy = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/health", timeout=1.0) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy

        # Verify saved_searches collection and records remain accessible
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/collections/saved_searches/records") as resp:
            assert resp.status == 200
            data = resp.read().decode("utf-8")
            assert "default_agent_search" in data

        # Verify superuser persists in database
        conn = sqlite3.connect(str(db_file))
        c = conn.cursor()
        c.execute("SELECT email FROM _superusers WHERE email = 'admin@test.local'")
        row = c.fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "admin@test.local"

    finally:
        proc2.terminate()
        proc2.wait(timeout=5)


def test_worktree_common_root_consistency():
    """Verify that resolve_git_common_root returns a valid directory across worktrees."""
    common_root = resolve_git_common_root()
    assert common_root.exists()
    assert (common_root / ".boss_agent").exists() or (common_root / ".git").exists()


def test_pocketbase_script_is_canonical_and_has_no_root_alias():
    """Verify `pocketbase.sh` is the one canonical script and no short alias shadows it.

    The root-level `pb.sh` symlink used to exist as a convenience, but it made
    `./pb<Tab>` ambiguous in the terminal and left two names for one runner. The
    `./run.sh pb` route covers the short form, so the root must stay free of aliases.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    pocketbase_sh = repo_root / "pocketbase.sh"

    assert pocketbase_sh.exists(), "pocketbase.sh must exist"
    assert pocketbase_sh.is_file(), "pocketbase.sh must be a regular file"
    assert not pocketbase_sh.is_symlink(), "pocketbase.sh must not be a symlink"
    assert os.access(pocketbase_sh, os.X_OK), "pocketbase.sh must stay executable"

    assert not (repo_root / "pb.sh").exists(), (
        "pb.sh must be gone: a root-level alias collides with the canonical name under "
        "shell tab-completion (issue #360)"
    )


def test_no_root_level_script_aliases_survive():
    """Verify every runner is reachable under exactly one name.

    A root-level short alias (`app.sh`, `emu.sh`, `pb.sh`, `wk.sh`) shared a prefix with
    its canonical script, so tab-completing that prefix stalled on two candidates instead
    of completing (issue #360).
    """
    repo_root = Path(__file__).resolve().parent.parent.parent

    removed_aliases = ("app.sh", "emu.sh", "pb.sh", "wk.sh")
    still_present = [name for name in removed_aliases if (repo_root / name).exists()]
    assert not still_present, f"root-level script aliases must be removed: {still_present}"

    # Positive control: the canonical scripts are still there, so this guard cannot pass
    # by finding an empty or wrong project root.
    for canonical in ("appium.sh", "emulator.sh", "pocketbase.sh", "worker.sh"):
        assert (repo_root / canonical).is_file(), f"{canonical} must exist"
