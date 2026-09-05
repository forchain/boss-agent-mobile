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
    assert "saved_searches" in collections
    assert collections["automation_tasks"] == ""
    assert collections["candidate_profiles"] == ""
    assert collections["saved_searches"] == ""

    # 5. Running provisioning again should idempotently update without error
    assert provision_sqlite_database(db_file) is True


def test_provision_sqlite_database_seeds_saved_searches(tmp_path: Path):
    db_file = tmp_path / "data.db"
    yaml_file = tmp_path / "searches.yaml"
    yaml_file.write_text(
        """
searches:
  test_search_1:
    name: "Test Search One"
    description: "First test search"
    search:
      keyword: "python"
    filter:
      education: "本科"
      salary: "3-5万"
      experience: "3-5年"
      activity: "今日活跃"
      company_scales:
        - "100-499人"
      industries:
        - "人工智能"
""",
        encoding="utf-8",
    )

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

    # Provision with custom YAML path
    res = provision_sqlite_database(db_file, searches_yaml_path=yaml_file)
    assert res is True

    # Verify seeded row in saved_searches
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, keyword, filter FROM saved_searches WHERE id = 'test_search_1'")
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "test_search_1"
    assert row[1] == "Test Search One"
    assert row[2] == "python"
    import json
    filter_data = json.loads(row[3])
    assert filter_data["education"] == "本科"
    assert filter_data["industries"] == ["人工智能"]

