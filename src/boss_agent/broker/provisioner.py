"""
src/boss_agent/broker/provisioner.py
====================================
PocketBase SQLite database and collection provisioner.
Ensures required collections (automation_tasks, candidate_profiles, saved_searches) exist
with public access rules and proper field definitions, and seeds default initial searches.
Supports both local SQLite direct provisioning and remote PocketBase REST API provisioning.
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure src/ is in sys.path when executed directly
_src_root = str(Path(__file__).resolve().parent.parent.parent)
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

# requests and urllib3 are lazily imported in provision_remote_pocketbase

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
    {"name": "enable_search", "type": "bool", "required": False},
    {"name": "enable_filter", "type": "bool", "required": False},
    {"name": "filter", "type": "json", "required": False},
    {"name": "cron_expression", "type": "text", "required": False},
    {"name": "is_enabled", "type": "bool", "required": False},
    {"name": "last_run_at", "type": "date", "required": False},
    {"name": "target_task_type", "type": "text", "required": False},
    {"name": "created", "type": "autodate", "onCreate": True},
    {"name": "updated", "type": "autodate", "onCreate": True, "onUpdate": True},
]

DEFAULT_INITIAL_SEARCHES: dict[str, dict[str, Any]] = {
    "default_agent_search": {
        "name": "AI Agent Default Startup Search",
        "description": "Default search query targeting Agent roles across Online Education, Gaming, and AI industries",
        "keyword": "agent",
        "enable_search": True,
        "enable_filter": True,
        "filter": {
            "education": "硕士",
            "salary": "5万元以上",
            "experience": "10年以上",
            "activity": "今日活跃",
            "company_scales": [
                "100-499人",
                "500-999人",
                "1000-9999人",
                "10000人以上",
            ],
            "industries": [
                "在线教育",
                "游戏",
                "人工智能",
            ],
        },
        "cron_expression": "",
        "is_enabled": False,
        "target_task_type": "AUTO_APPLY",
    },
    "ai_llm_engineer": {
        "name": "AI & LLM Engineer Search",
        "description": "Search targeting Large Language Model and AI algorithm engineering positions",
        "keyword": "大模型算法",
        "enable_search": True,
        "enable_filter": True,
        "filter": {
            "education": "硕士",
            "salary": "5万元以上",
            "experience": "5-10年",
            "activity": "今日活跃",
            "company_scales": [
                "500-999人",
                "1000-9999人",
                "10000人以上",
            ],
            "industries": [
                "人工智能",
                "游戏",
                "在线教育",
            ],
        },
        "cron_expression": "",
        "is_enabled": False,
        "target_task_type": "AUTO_APPLY",
    },
}


from boss_agent.settings import resolve_pocketbase_db_path


def provision_sqlite_database(
    db_path: str | Path | None = None,
    initial_searches: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Initialize or update PocketBase SQLite schema for required collections.

    If db_path is not specified, it will be automatically resolved from configuration
    (config/settings.local.yaml, config/settings.yaml), env vars (PB_DB_PATH, PB_DATA_DIR),
    or the default fallback path.
    """
    resolved_path = resolve_pocketbase_db_path(db_path)
    db_file = Path(resolved_path)
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
                    enable_search BOOLEAN DEFAULT 1,
                    enable_filter BOOLEAN DEFAULT 1,
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

        # Migrate existing saved_searches table if columns missing
        cursor.execute("PRAGMA table_info(saved_searches)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        migration_columns = [
            ("enable_search", "BOOLEAN DEFAULT 1"),
            ("enable_filter", "BOOLEAN DEFAULT 1"),
            ("cron_expression", "TEXT"),
            ("is_enabled", "BOOLEAN DEFAULT 0"),
            ("last_run_at", "TEXT"),
            ("target_task_type", "TEXT DEFAULT 'AUTO_APPLY'"),
        ]
        for col_name, col_type in migration_columns:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE saved_searches ADD COLUMN {col_name} {col_type}")

        # Seed initial saved searches if table is empty
        cursor.execute("SELECT COUNT(*) FROM saved_searches")
        count = cursor.fetchone()[0]
        if count == 0:
            seeds = initial_searches or DEFAULT_INITIAL_SEARCHES
            for search_id, item_data in seeds.items():
                s_name = item_data.get("name", search_id)
                s_desc = item_data.get("description", "")
                s_kw = item_data.get("keyword", "")
                s_en_search = 1 if item_data.get("enable_search", True) else 0
                s_en_filter = 1 if item_data.get("enable_filter", True) else 0
                s_filter = item_data.get("filter", {})
                s_cron = item_data.get("cron_expression", "")
                s_enabled = 1 if item_data.get("is_enabled", False) else 0
                s_type = item_data.get("target_task_type", "AUTO_APPLY")
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO saved_searches (
                        id, name, description, keyword, enable_search, enable_filter,
                        filter, cron_expression, is_enabled, target_task_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        search_id,
                        s_name,
                        s_desc,
                        s_kw,
                        s_en_search,
                        s_en_filter,
                        json.dumps(s_filter),
                        s_cron,
                        s_enabled,
                        s_type,
                    ),
                )
            logger.info("Successfully seeded %d saved_searches into SQLite", len(seeds))

        conn.commit()
        return True
    finally:
        conn.close()


def provision_remote_pocketbase(
    pb_url: str,
    email: str,
    password: str,
    timeout: float = 10.0,
    initial_searches: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Provision remote PocketBase collections and seed initial data using Admin/Superuser REST API."""
    try:
        import requests
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        logger.error("The 'requests' package is required for remote PocketBase provisioning.")
        print("❌ Error: 'requests' package is required for remote provisioning. Install via: pip install requests")
        return False
    base_url = pb_url.rstrip("/")
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    # 1. Superuser Auth (PocketBase v0.23+ uses _superusers collection, older versions use admins)
    auth_endpoints = [
        f"{base_url}/api/collections/_superusers/auth-with-password",
        f"{base_url}/api/admins/auth-with-password",
    ]
    token = None
    for endpoint in auth_endpoints:
        try:
            resp = session.post(
                endpoint,
                json={"identity": email, "password": password},
                timeout=timeout,
                verify=False,
            )
            if resp.ok:
                data = resp.json()
                token = data.get("token")
                if token:
                    break
        except Exception as ex:
            logger.debug("Auth endpoint %s failed: %s", endpoint, ex)

    if not token:
        logger.error("Failed to authenticate to PocketBase at %s as %s", pb_url, email)
        print(f"❌ Failed to authenticate to PocketBase at {pb_url} with email {email}")
        return False

    session.headers.update({"Authorization": token})
    print(f"✅ Authenticated successfully as superuser '{email}'")

    # 2. Fetch existing collections
    try:
        resp = session.get(f"{base_url}/api/collections", params={"perPage": 200}, timeout=timeout, verify=False)
        if not resp.ok:
            logger.error("Failed to list collections: %s", resp.text)
            print(f"❌ Failed to list collections: {resp.text}")
            return False
        collections_data = resp.json().get("items", [])
        existing_names = {c.get("name") for c in collections_data}
    except Exception as ex:
        logger.error("Error fetching collections from %s: %s", pb_url, ex)
        print(f"❌ Network error while querying collections: {ex}")
        return False

    # 3. Define collections to create
    collections_to_create = [
        {
            "id": "pbc_auto_tasks",
            "name": "automation_tasks",
            "type": "base",
            "listRule": "",
            "viewRule": "",
            "createRule": "",
            "updateRule": "",
            "deleteRule": "",
            "fields": [
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
            ],
        },
        {
            "id": "pbc_cand_prof",
            "name": "candidate_profiles",
            "type": "base",
            "listRule": "",
            "viewRule": "",
            "createRule": "",
            "updateRule": "",
            "deleteRule": "",
            "fields": [
                {"name": "user_id", "type": "text", "required": True},
                {"name": "name", "type": "text", "required": False},
                {"name": "years_of_experience", "type": "number", "required": False},
                {"name": "education", "type": "json", "required": False},
                {"name": "core_skills", "type": "json", "required": False},
                {"name": "project_highlights", "type": "json", "required": False},
                {"name": "target_positions", "type": "json", "required": False},
                {"name": "raw_summary", "type": "text", "required": False},
            ],
        },
        {
            "id": "pbc_saved_searches",
            "name": "saved_searches",
            "type": "base",
            "listRule": "",
            "viewRule": "",
            "createRule": "",
            "updateRule": "",
            "deleteRule": "",
            "fields": [
                {"name": "name", "type": "text", "required": True},
                {"name": "description", "type": "text", "required": False},
                {"name": "keyword", "type": "text", "required": False},
                {"name": "enable_search", "type": "bool", "required": False},
                {"name": "enable_filter", "type": "bool", "required": False},
                {"name": "filter", "type": "json", "required": False},
                {"name": "cron_expression", "type": "text", "required": False},
                {"name": "is_enabled", "type": "bool", "required": False},
                {"name": "last_run_at", "type": "date", "required": False},
                {"name": "target_task_type", "type": "text", "required": False},
            ],
        },
    ]

    for col in collections_to_create:
        c_name = col["name"]
        if c_name not in existing_names:
            create_resp = session.post(f"{base_url}/api/collections", json=col, timeout=timeout, verify=False)
            if create_resp.ok:
                logger.info("Created collection '%s' via REST API", c_name)
                print(f"✨ Created collection '{c_name}' successfully")
            else:
                logger.error("Failed to create collection '%s': %s", c_name, create_resp.text)
                print(f"❌ Failed to create collection '{c_name}': {create_resp.text}")
        else:
            print(f"ℹ️ Collection '{c_name}' already exists")

    # 4. Seed saved_searches if empty
    try:
        check_records = session.get(
            f"{base_url}/api/collections/saved_searches/records",
            params={"perPage": 1},
            timeout=timeout,
            verify=False,
        )
        if check_records.ok:
            total_items = check_records.json().get("totalItems", 0)
            if total_items == 0:
                seeds = initial_searches or DEFAULT_INITIAL_SEARCHES
                for s_id, s_data in seeds.items():
                    record_payload = {
                        "id": s_id,
                        "name": s_data.get("name", s_id),
                        "description": s_data.get("description", ""),
                        "keyword": s_data.get("keyword", ""),
                        "enable_search": s_data.get("enable_search", True),
                        "enable_filter": s_data.get("enable_filter", True),
                        "filter": s_data.get("filter", {}),
                        "cron_expression": s_data.get("cron_expression", ""),
                        "is_enabled": s_data.get("is_enabled", False),
                        "target_task_type": s_data.get("target_task_type", "AUTO_APPLY"),
                    }
                    seed_resp = session.post(
                        f"{base_url}/api/collections/saved_searches/records",
                        json=record_payload,
                        timeout=timeout,
                        verify=False,
                    )
                    if seed_resp.ok:
                        print(f"🌱 Seeded saved search '{s_id}' successfully")
                    else:
                        print(f"⚠️ Failed to seed '{s_id}': {seed_resp.text}")
            else:
                print(f"ℹ️ 'saved_searches' already has {total_items} records, skipping seeding.")
    except Exception as ex:
        logger.warning("Error checking/seeding saved_searches records: %s", ex)
        print(f"⚠️ Error checking/seeding records: {ex}")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PocketBase Collection and SQLite Provisioner")
    parser.add_argument(
        "db_path",
        nargs="?",
        default=None,
        help="Path to local data.db SQLite file (defaults to configured pocketbase_db_path)",
    )
    parser.add_argument("--url", help="Remote PocketBase URL, e.g. https://pocketbase.chainer.tech:4433")
    parser.add_argument("--email", help="Superuser / Admin email")
    parser.add_argument("--password", help="Superuser / Admin password")

    args = parser.parse_args()

    if args.url and args.email and args.password:
        print(f"🚀 Provisioning remote PocketBase at {args.url} ...")
        res = provision_remote_pocketbase(args.url, args.email, args.password)
        print(f"Provisioning result: {res}")
        sys.exit(0 if res else 1)
    else:
        target_db = resolve_pocketbase_db_path(args.db_path)
        print(f"🚀 Provisioning local SQLite database at {target_db} ...")
        res = provision_sqlite_database(target_db)
        print(f"Provisioning status: {res}")
        sys.exit(0 if res else 1)
