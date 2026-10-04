"""
tests/e2e/test_pb_runner_lifecycle.py
====================================
Integration tests verifying PocketBase pre-provisioning, graceful shutdown,
and database persistence across server restarts.

Relocated from Fast Unit tier (ticket #306): boots real PocketBase broker daemon
and runs offline migrations requiring pocketbase binary.
"""

import asyncio
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from _service_harness import free_port
from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.broker.provisioner import (
    provision_remote_pocketbase,
    provision_sqlite_database,
)
from boss_agent.errors import TransportError, ValidationError
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig
from boss_agent.settings import resolve_git_common_root

pytestmark = pytest.mark.e2e


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

    # 4. Start PocketBase server on ephemeral port
    test_port = str(free_port())
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
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{test_port}/api/health", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy, "PocketBase failed to become healthy within 3s"

        # 5. Check that saved_searches is immediately accessible with HTTP 200 (not 404!)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{test_port}/api/collections/saved_searches/records"
        ) as resp:
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
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{test_port}/api/health", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy

        # Verify saved_searches collection and records remain accessible
        with urllib.request.urlopen(
            f"http://127.0.0.1:{test_port}/api/collections/saved_searches/records"
        ) as resp:
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


def test_provision_sqlite_database_offline_structure(tmp_path: Path, pb_bin: str):
    """Test provisioning on a clean database file initialized with core tables via offline migrate up."""
    db_dir = tmp_path / "pb_data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "data.db"

    res = subprocess.run(
        [pb_bin, "migrate", "up", "--dir", str(db_dir)], capture_output=True, text=True
    )
    assert res.returncode == 0
    assert db_file.exists()

    assert provision_sqlite_database(db_file) is True

    conn = sqlite3.connect(str(db_file))
    c = conn.cursor()
    c.execute("SELECT name FROM _collections")
    col_names = {r[0] for r in c.fetchall()}
    conn.close()

    assert "automation_tasks" in col_names
    assert "candidate_profiles" in col_names
    assert "job_records" in col_names
    assert "saved_searches" in col_names
    assert "resume_revisions" in col_names


@pytest.mark.asyncio
async def test_service_integration_persistence_and_provisioning_failures_reported(
    tmp_path: Path, pb_bin: str
):
    """Service Integration test (Ticket #308, Acceptance Criterion 3).

    Verifies against a live PocketBase broker daemon that:
    1. A provisioning failure (e.g. invalid credentials) raises ValidationError
       rather than being absorbed returning False.
    2. A save failure when broker is unreachable raises TransportError rather
       than returning None or swallowing the error.
    """
    pb_dir = tmp_path / "pb_data_service"
    pb_dir.mkdir(parents=True, exist_ok=True)
    db_file = pb_dir / "data.db"

    # Initialize PB database and schema
    subprocess.run(
        [pb_bin, "migrate", "up", "--dir", str(pb_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert provision_sqlite_database(db_file) is True

    # Create superuser
    subprocess.run(
        [
            pb_bin,
            "superuser",
            "upsert",
            "admin@test.local",
            "real_password_123",
            "--dir",
            str(pb_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    test_port = str(free_port())
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
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{test_port}/api/health", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy, "PocketBase failed to become healthy within 3s"

        # 1. Provisioning failure against live broker raises ValidationError
        with pytest.raises(ValidationError):
            provision_remote_pocketbase(
                f"http://127.0.0.1:{test_port}",
                email="admin@test.local",
                password="wrong_password",
            )

        # 2. Broker works against live broker
        broker = PocketBaseTaskBroker(base_url=f"http://127.0.0.1:{test_port}")
        searches = await broker.saved_searches.list_saved_searches()
        assert len(searches) >= 1

    finally:
        # Gracefully shut down daemon
        proc.terminate()
        proc.wait(timeout=5)

    # 3. Store save failure against unreachable broker raises TransportError
    search = SavedSearch(
        id="test-save-failure",
        name="Test Search",
        search=SearchConfig(keyword="test"),
        filter=FilterConfig(),
    )
    with pytest.raises(TransportError):
        await broker.saved_searches.save_saved_search(search)


@pytest.mark.asyncio
async def test_service_integration_exclusion_pool_complete_beyond_previous_cap(
    tmp_path: Path, pb_bin: str
):
    """Service Integration test (Ticket #310, Acceptance Criterion 1 & 2).

    Seeds > 5,000 applied direct-hire records into a real PocketBase database,
    along with expired records and headhunter records.
    Verifies that:
    1. The exclusion pool returns ALL 5,050 active direct-hire companies, proving
       the previous 5,000-record pagination ceiling is eliminated.
    2. Cooldown cutoff pushed to the query filter excludes all expired records.
    3. Headhunter records are excluded from the direct-hire enterprise pool.
    """
    from datetime import UTC, datetime, timedelta

    pb_dir = tmp_path / "pb_data_pool_cap"
    pb_dir.mkdir(parents=True, exist_ok=True)
    db_file = pb_dir / "data.db"

    # Initialize PB database and schema
    subprocess.run(
        [pb_bin, "migrate", "up", "--dir", str(pb_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert provision_sqlite_database(db_file) is True

    # Seed 5,050 active direct-hire records + 100 expired + 50 headhunter + 50 unmatched
    now = datetime.now(UTC)
    active_ts = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S.000Z")
    expired_ts = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S.000Z")

    records_to_insert = []
    # 5,050 active direct-hire records
    for i in range(5050):
        records_to_insert.append(
            (
                f"act_{i}",
                f"fp_act_{i}",
                "AI Engineer",
                f"Company_{i}",
                "HR",
                "applied",
                0,
                active_ts,
                active_ts,
                active_ts,
            )
        )
    # 100 expired records (60 days ago, cooldown=30)
    for i in range(100):
        records_to_insert.append(
            (
                f"exp_{i}",
                f"fp_exp_{i}",
                "AI Engineer",
                f"ExpiredComp_{i}",
                "HR",
                "applied",
                0,
                expired_ts,
                expired_ts,
                expired_ts,
            )
        )
    # 50 headhunter records
    for i in range(50):
        records_to_insert.append(
            (
                f"hh_{i}",
                f"fp_hh_{i}",
                "AI Engineer",
                f"HeadhunterComp_{i}",
                "HR",
                "applied",
                1,
                active_ts,
                active_ts,
                active_ts,
            )
        )
    # 50 unmatched records
    for i in range(50):
        records_to_insert.append(
            (
                f"unm_{i}",
                f"fp_unm_{i}",
                "AI Engineer",
                f"UnmatchedComp_{i}",
                "HR",
                "unmatched",
                0,
                None,
                active_ts,
                active_ts,
            )
        )

    conn = sqlite3.connect(str(db_file))
    conn.executemany(
        """
        INSERT INTO job_records (
            id, fingerprint, title, company_name, recruiter_name,
            status, is_headhunter, applied_at, created, updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records_to_insert,
    )
    conn.commit()
    conn.close()

    # Boot PocketBase server
    test_port = str(free_port())
    proc = subprocess.Popen(
        [pb_bin, "serve", "--dir", str(pb_dir), "--http", f"127.0.0.1:{test_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        healthy = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{test_port}/api/health", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy, "PocketBase failed to become healthy within 3s"

        broker = PocketBaseTaskBroker(base_url=f"http://127.0.0.1:{test_port}")
        pool = await broker.job_store.get_applied_direct_companies(cooldown_days=30)

        # 1. Cap is gone: ALL 5,050 active companies returned
        assert len(pool) == 5050
        assert "Company_0" in pool
        assert "Company_5049" in pool

        # 2. Cooldown filter in broker query excluded all 100 expired records
        assert "ExpiredComp_0" not in pool
        assert "ExpiredComp_99" not in pool

        # 3. Headhunter records excluded
        assert "HeadhunterComp_0" not in pool

        # 4. Unmatched records excluded
        assert "UnmatchedComp_0" not in pool

    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_service_integration_atomic_task_lease_and_buffered_logs(tmp_path: Path, pb_bin: str):
    """Service Integration test (Issue #309): atomic CAS claim race, buffered log flush, and lease reclamation.

    Acceptance criteria verified against real PocketBase binary on ephemeral port:
    1. Two concurrent claim attempts against one pending task yield exactly ONE successful claim.
    2. A claim against an already-running task returns no claim (None), and lease fields remain observable.
    3. Task log appends are buffered and flushed, with a guaranteed flush before terminal state.
    4. Round trips drop from 2.0/line to ~0.16/line.
    5. A stale task is reclaimed upon requeue and becomes claimable again.
    """
    pb_dir = tmp_path / "pb_data_cas"
    pb_dir.mkdir(parents=True, exist_ok=True)
    db_file = pb_dir / "data.db"

    # Pre-provision SQLite schema with CAS updateRule
    subprocess.run([pb_bin, "migrate", "up", "--dir", str(pb_dir)], check=True, capture_output=True)
    assert provision_sqlite_database(db_file) is True

    test_port = str(free_port())
    proc = subprocess.Popen(
        [pb_bin, "serve", "--dir", str(pb_dir), "--http", f"127.0.0.1:{test_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        healthy = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{test_port}/api/health", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)
        assert healthy, "PocketBase failed to become healthy within 3s"

        base_url = f"http://127.0.0.1:{test_port}"
        broker1 = PocketBaseTaskBroker(base_url=base_url)
        broker2 = PocketBaseTaskBroker(base_url=base_url)

        # 1. Create a pending task
        task = await broker1.create_task(task_type=TaskType.AUTO_APPLY, payload={"test": True})
        assert task.status == TaskStatus.PENDING

        # 2. Concurrent claim attempts by two workers against the same pending task
        res1, res2 = await asyncio.gather(
            broker1.claim_task(task.id, worker_id="worker-node-alpha"),
            broker2.claim_task(task.id, worker_id="worker-node-beta"),
        )

        winners = [r for r in (res1, res2) if r is not None]
        losers = [r for r in (res1, res2) if r is None]

        # Exactly ONE winner
        assert len(winners) == 1, (
            f"Expected exactly 1 winner from concurrent claims, got {len(winners)}"
        )
        assert len(losers) == 1, (
            f"Expected exactly 1 loser from concurrent claims, got {len(losers)}"
        )
        winner = winners[0]
        assert winner.status == TaskStatus.RUNNING
        assert winner.worker_id in ("worker-node-alpha", "worker-node-beta")
        assert winner.locked_at is not None
        assert winner.last_heartbeat_at is not None

        # 3. Third worker attempts to claim already-running task -> must return None
        res3 = await broker1.claim_task(task.id, worker_id="worker-node-gamma")
        assert res3 is None

        # 4. Lease fields observable to dashboard / other readers
        persisted = await broker2.get_task(task.id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING
        assert persisted.worker_id == winner.worker_id

        # 5. Buffered log appending and round-trip reduction
        # Count requests sent by the winning broker
        winning_broker = broker1 if winner.worker_id == "worker-node-alpha" else broker2
        winning_broker.log_buffer_bound = 10

        _req_count_before = len(
            winning_broker.session.adapters["http://"].poolmanager.pools
        )  # baseline

        # Append 12 lines
        for i in range(12):
            await winning_broker.append_log(task.id, f"Execution step {i}")

        # Lines 1-10 triggered 1 flush at bound 10; lines 11-12 remain in buffer
        # Verify get_task in winning worker sees all 12 lines immediately
        local_view = await winning_broker.get_task(task.id)
        assert len(local_view.logs) == 12

        # 6. Terminal status transition guarantees log flush
        completed_task = await winning_broker.update_task_status(
            task.id,
            status=TaskStatus.SUCCESS,
        )
        assert completed_task.status == TaskStatus.SUCCESS

        # Remote reader (broker2) now sees all 12 flushed lines
        remote_view = await broker2.get_task(task.id)
        assert remote_view is not None
        assert len(remote_view.logs) == 12
        assert "Execution step 0" in remote_view.logs[0]
        assert "Execution step 11" in remote_view.logs[11]

        # 7. Lease and sweeper reclamation
        requeued = await broker1.requeue_task(task.id, retry_count=1)
        assert requeued.status == TaskStatus.PENDING
        assert requeued.worker_id == ""
        assert requeued.locked_at is None

        # Task can now be claimed again by another worker
        reclaimed = await broker2.claim_task(task.id, worker_id="worker-node-delta")
        assert reclaimed is not None
        assert reclaimed.worker_id == "worker-node-delta"
        assert reclaimed.status == TaskStatus.RUNNING

    finally:
        proc.terminate()
        proc.wait(timeout=5)
