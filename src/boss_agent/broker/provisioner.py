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

SAVED_SEARCHES_FIELDS = [
    {"name": "id", "type": "text", "primaryKey": True, "required": False},
    {"name": "name", "type": "text", "required": True},
    {"name": "description", "type": "text", "required": False},
    {"name": "keyword", "type": "text", "required": False},
    {"name": "filter", "type": "json", "required": False},
    {"name": "cron_expression", "type": "text", "required": False},
    {"name": "is_enabled", "type": "bool", "required": False},
    {"name": "last_run_at", "type": "date", "required": False},
    {"name": "target_task_type", "type": "text", "required": False},
    {"name": "created", "type": "autodate", "onCreate": True},
    {"name": "updated", "type": "autodate", "onCreate": True, "onUpdate": True},
]


def _find_default_searches_yaml() -> Path | None:
    possible = [
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "searches.yaml",
        Path("config/searches.yaml"),
    ]
    for p in possible:
        if p.exists():
            return p
    return None


def provision_sqlite_database(
    db_path: str | Path = ".boss_agent/pb_data/data.db",
    searches_yaml_path: str | Path | None = None,
) -> bool:
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
        saved_searches_json = json.dumps(SAVED_SEARCHES_FIELDS)

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

        if "saved_searches" not in existing:
            cursor.execute(
                """
                INSERT INTO _collections (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
                VALUES ('pbc_saved_searches', 0, 'base', 'saved_searches', ?, '', '', '', '', '')
                """,
                (saved_searches_json,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_searches (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    keyword TEXT,
                    filter JSON,
                    cron_expression TEXT,
                    is_enabled BOOLEAN DEFAULT 0,
                    last_run_at TEXT,
                    target_task_type TEXT DEFAULT 'AUTO_APPLY',
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
                WHERE name = 'saved_searches'
                """,
                (saved_searches_json,),
            )

        # Auto-seed from YAML if table is empty
        cursor.execute("SELECT COUNT(*) FROM saved_searches")
        count = cursor.fetchone()[0]
        if count == 0:
            yaml_target = Path(searches_yaml_path) if searches_yaml_path else _find_default_searches_yaml()
            if yaml_target and yaml_target.exists():
                try:
                    import yaml

                    content = yaml_target.read_text(encoding="utf-8")
                    parsed = yaml.safe_load(content) or {}
                    searches_dict = parsed.get("searches", parsed)
                    for search_id, item_data in searches_dict.items():
                        if isinstance(item_data, dict):
                            s_name = item_data.get("name", search_id)
                            s_desc = item_data.get("description", "")
                            s_kw = item_data.get("search", {}).get("keyword", "")
                            s_filter = item_data.get("filter", {})
                            cursor.execute(
                                """
                                INSERT OR IGNORE INTO saved_searches (
                                    id, name, description, keyword, filter, cron_expression, is_enabled, target_task_type
                                ) VALUES (?, ?, ?, ?, ?, '', 0, 'AUTO_APPLY')
                                """,
                                (search_id, s_name, s_desc, s_kw, json.dumps(s_filter)),
                            )
                    logger.info("Successfully seeded %d saved_searches from %s", len(searches_dict), yaml_target)
                except Exception as ex:
                    logger.warning("Failed to auto-seed saved_searches from YAML: %s", ex)

        conn.commit()
        return True
    finally:
        conn.close()



if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ".boss_agent/pb_data/data.db"
    res = provision_sqlite_database(target)
    print(f"Provisioning status: {res}")
