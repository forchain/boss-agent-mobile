"""
tests/unit/test_provisioner.py
==============================
Unit tests for PocketBase SQLite database provisioner and schema setup.
"""

import sqlite3
from pathlib import Path

from boss_agent.broker.provisioner import provision_sqlite_database


def test_provision_sqlite_database(tmp_path: Path):
    db_file = tmp_path / "data.db"

    # 1. Non-existent file should return False gracefully
    assert provision_sqlite_database(db_file) is False

    # 2. Initialize minimal PocketBase SQLite database with _collections
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE _collections (
            id TEXT PRIMARY KEY,
            system BOOLEAN DEFAULT FALSE,
            type TEXT DEFAULT "base",
            name TEXT UNIQUE NOT NULL,
            fields JSON DEFAULT "[]" NOT NULL,
            indexes JSON DEFAULT "[]" NOT NULL,
            listRule TEXT DEFAULT NULL,
            viewRule TEXT DEFAULT NULL,
            createRule TEXT DEFAULT NULL,
            updateRule TEXT DEFAULT NULL,
            deleteRule TEXT DEFAULT NULL,
            options JSON DEFAULT "{}" NOT NULL,
            created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ')),
            updated TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ'))
        )
    """)
    conn.commit()
    conn.close()

    # 3. Provisioning should succeed and create tables
    res = provision_sqlite_database(db_file)
    assert res is True

    # 4. Verify collections were created with public access rules
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT name, listRule FROM _collections")
    collections = dict(cursor.fetchall())
    conn.close()

    assert "automation_tasks" in collections
    assert "candidate_profiles" in collections
    assert collections["automation_tasks"] == ""
    assert collections["candidate_profiles"] == ""

    # 5. Running provisioning again should idempotently update without error
    assert provision_sqlite_database(db_file) is True
