"""
src/boss_agent/broker/provisioner.py
====================================
PocketBase SQLite database and collection provisioner.
Ensures required collections (automation_tasks, candidate_profiles) exist
with public access rules and proper field definitions.
"""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("boss_agent.broker.provisioner")

AUTOMATION_TASKS_FIELDS = [
    {"name": "id", "type": "text", "primaryKey": True, "required": False},
    {"name": "task_type", "type": "text", "required": True},
    {"name": "status", "type": "text", "required": True},
    {"name": "payload", "type": "json", "required": False},
    {"name": "worker_id", "type": "text", "required": False},
    {"name": "locked_at", "type": "date", "required": False},
    {"name": "last_heartbeat_at", "type": "date", "required": False},
    {"name": "retry_count", "type": "number", "required": False},
    {"name": "logs", "type": "json", "required": False},
    {"name": "error_message", "type": "text", "required": False},
    {"name": "assigned_worker", "type": "text", "required": False},
    {"name": "created", "type": "autodate", "onCreate": True},
    {"name": "updated", "type": "autodate", "onCreate": True, "onUpdate": True},
]

CANDIDATE_PROFILES_FIELDS = [
    {"name": "id", "type": "text", "primaryKey": True, "required": False},
    {"name": "user_id", "type": "text", "required": True},
    {"name": "name", "type": "text", "required": False},
    {"name": "years_of_experience", "type": "number", "required": False},
    {"name": "education", "type": "json", "required": False},
    {"name": "core_skills", "type": "json", "required": False},
    {"name": "project_highlights", "type": "json", "required": False},
    {"name": "target_positions", "type": "json", "required": False},
    {"name": "raw_summary", "type": "text", "required": False},
    {"name": "created", "type": "autodate", "onCreate": True},
    {"name": "updated", "type": "autodate", "onCreate": True, "onUpdate": True},
]


def provision_sqlite_database(db_path: str | Path = ".boss_agent/pb_data/data.db") -> bool:
    """Initialize or update PocketBase SQLite schema for required collections."""
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    if not db_file.exists():
        return False

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_collections'")
        if not cursor.fetchone():
            return False

        cursor.execute("SELECT name FROM _collections")
        existing = {row[0] for row in cursor.fetchall()}

        auto_tasks_json = json.dumps(AUTOMATION_TASKS_FIELDS)
        cand_prof_json = json.dumps(CANDIDATE_PROFILES_FIELDS)

        if "automation_tasks" not in existing:
            cursor.execute(
                """
                INSERT INTO _collections (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
                VALUES ('pbc_auto_tasks', 0, 'base', 'automation_tasks', ?, '', '', '', '', '')
                """,
                (auto_tasks_json,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_tasks (
                    id TEXT PRIMARY KEY,
                    task_type TEXT,
                    status TEXT,
                    payload JSON,
                    worker_id TEXT,
                    locked_at TEXT,
                    last_heartbeat_at TEXT,
                    retry_count INTEGER DEFAULT 0,
                    logs JSON,
                    error_message TEXT,
                    assigned_worker TEXT,
                    created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ')),
                    updated TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ'))
                )
                """
            )
        else:
            cursor.execute(
                """
                UPDATE _collections
                SET fields = ?, listRule = '', viewRule = '', createRule = '', updateRule = '', deleteRule = ''
                WHERE name = 'automation_tasks'
                """,
                (auto_tasks_json,),
            )

        if "candidate_profiles" not in existing:
            cursor.execute(
                """
                INSERT INTO _collections (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
                VALUES ('pbc_cand_prof', 0, 'base', 'candidate_profiles', ?, '', '', '', '', '')
                """,
                (cand_prof_json,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    name TEXT,
                    years_of_experience INTEGER,
                    education JSON,
                    core_skills JSON,
                    project_highlights JSON,
                    target_positions JSON,
                    raw_summary TEXT,
                    created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ')),
                    updated TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ'))
                )
                """
            )
        else:
            cursor.execute(
                """
                UPDATE _collections
                SET fields = ?, listRule = '', viewRule = '', createRule = '', updateRule = '', deleteRule = ''
                WHERE name = 'candidate_profiles'
                """,
                (cand_prof_json,),
            )

        conn.commit()
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ".boss_agent/pb_data/data.db"
    res = provision_sqlite_database(target)
    print(f"Provisioning status: {res}")
