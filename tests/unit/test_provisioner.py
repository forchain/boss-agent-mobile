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

    custom_initial_searches = {
        "test_search_1": {
            "name": "Test Search One",
            "description": "First test search",
            "keyword": "python",
            "enable_search": True,
            "enable_filter": False,
            "filter": {
                "education": "本科",
                "industries": ["人工智能"],
            },
            "cron_expression": "",
            "is_enabled": False,
            "target_task_type": "AUTO_APPLY",
        }
    }

    # Provision with custom initial searches
    res = provision_sqlite_database(db_file, initial_searches=custom_initial_searches)
    assert res is True

    # Verify seeded row in saved_searches
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, keyword, enable_search, enable_filter, filter FROM saved_searches WHERE id = 'test_search_1'")
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "test_search_1"
    assert row[1] == "Test Search One"
    assert row[2] == "python"
    assert row[3] == 1
    assert row[4] == 0
    import json
    filter_data = json.loads(row[5])
    assert filter_data["education"] == "本科"
    assert filter_data["industries"] == ["人工智能"]


def test_provision_sqlite_database_migrates_existing_table(tmp_path: Path):
    """If saved_searches table exists without enable_search/enable_filter, they are added."""
    db_file = tmp_path / "legacy.db"
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
            options JSON DEFAULT "{}" NOT NULL
        )
    """)
    # Pre-create older saved_searches schema missing enable_search & enable_filter
    cursor.execute("""
        CREATE TABLE saved_searches (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            keyword TEXT,
            filter JSON
        )
    """)
    cursor.execute("INSERT INTO _collections (id, name, fields) VALUES ('pbc_saved_searches', 'saved_searches', '[]')")
    conn.commit()
    conn.close()

    # Run provisioner on legacy DB
    assert provision_sqlite_database(db_file) is True

    # Check migrated columns
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(saved_searches)")
    col_names = [col[1] for col in cursor.fetchall()]
    conn.close()

    assert "enable_search" in col_names
    assert "enable_filter" in col_names


def test_provision_remote_pocketbase_mock():
    from unittest.mock import MagicMock, patch

    from boss_agent.broker.provisioner import provision_remote_pocketbase

    mock_session = MagicMock()
    # Mock Auth
    auth_resp = MagicMock(ok=True)
    auth_resp.json.return_value = {"token": "test_token"}

    # Mock Collections list
    list_col_resp = MagicMock(ok=True)
    list_col_resp.json.return_value = {"items": [{"name": "users"}]}

    # Mock Create Collection
    create_col_resp = MagicMock(ok=True)

    # Mock Records check
    records_resp = MagicMock(ok=True)
    records_resp.json.return_value = {"totalItems": 0}

    # Mock Seed record
    seed_resp = MagicMock(ok=True)

    def mock_post(url, **kwargs):
        if "auth-with-password" in url:
            return auth_resp
        if "collections/saved_searches/records" in url:
            return seed_resp
        return create_col_resp

    def mock_get(url, **kwargs):
        if url.endswith("/api/collections"):
            return list_col_resp
        return records_resp

    mock_session.post.side_effect = mock_post
    mock_session.get.side_effect = mock_get

    with patch("requests.Session", return_value=mock_session):
        success = provision_remote_pocketbase(
            "http://127.0.0.1:8090",
            email="admin@example.com",
            password="password123",
        )
        assert success is True
        assert mock_session.post.call_count >= 1


def test_provision_sqlite_database_offline_structure(tmp_path: Path):
    """Test provisioning on a clean database file initialized with core tables."""
    import shutil
    import subprocess

    db_dir = tmp_path / "pb_data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "data.db"

    pb_bin = shutil.which("pocketbase")
    if pb_bin:
        # Run pocketbase migrate up offline to create initial tables
        res = subprocess.run([pb_bin, "migrate", "up", "--dir", str(db_dir)], capture_output=True, text=True)
        assert res.returncode == 0
        assert db_file.exists()

        # Run provision_sqlite_database
        assert provision_sqlite_database(db_file) is True

        conn = sqlite3.connect(str(db_file))
        c = conn.cursor()
        c.execute("SELECT name FROM _collections")
        col_names = {r[0] for r in c.fetchall()}
        conn.close()

        assert "automation_tasks" in col_names
        assert "candidate_profiles" in col_names
        assert "saved_searches" in col_names


