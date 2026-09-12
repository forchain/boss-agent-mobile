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
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure src/ is in sys.path when executed directly
_src_root = str(Path(__file__).resolve().parent.parent.parent)
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from boss_agent.settings import resolve_pocketbase_db_path  # noqa: E402

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
    {"name": "work_experiences", "type": "json", "required": False},
    {"name": "projects", "type": "json", "required": False},
    {"name": "target_positions", "type": "json", "required": False},
    {"name": "raw_summary", "type": "text", "required": False},
    {"name": "raw_resume_text", "type": "text", "required": False},
    {"name": "created", "type": "autodate", "onCreate": True},
    {"name": "updated", "type": "autodate", "onCreate": True, "onUpdate": True},
]

RESUME_REVISIONS_FIELDS = [
    {"name": "id", "type": "text", "primaryKey": True, "required": False},
    {"name": "user_id", "type": "text", "required": True},
    {"name": "file_name", "type": "text", "required": True},
    {"name": "file_type", "type": "text", "required": False},
    {"name": "file_size", "type": "number", "required": False},
    {"name": "extracted_text", "type": "text", "required": False},
    {"name": "diff_summary", "type": "text", "required": False},
    {"name": "created", "type": "autodate", "onCreate": True},
    {"name": "updated", "type": "autodate", "onCreate": True, "onUpdate": True},
]

JOB_RECORDS_FIELDS = [
    {"name": "id", "type": "text", "primaryKey": True, "required": False},
    {"name": "fingerprint", "type": "text", "required": True},
    {"name": "title", "type": "text", "required": True},
    {"name": "company_name", "type": "text", "required": True},
    {"name": "recruiter_name", "type": "text", "required": True},
    {"name": "salary_range", "type": "text", "required": False},
    {"name": "location", "type": "text", "required": False},
    {"name": "digest", "type": "text", "required": False},
    {"name": "job_description", "type": "text", "required": False},
    {"name": "company_scale", "type": "text", "required": False},
    {"name": "industry", "type": "text", "required": False},
    {"name": "tags", "type": "json", "required": False},
    {"name": "recruiter_title", "type": "text", "required": False},
    {"name": "is_headhunter", "type": "bool", "required": False},
    {"name": "status", "type": "text", "required": True},
    {"name": "match_score", "type": "number", "required": False},
    {"name": "jd_key_requirements", "type": "json", "required": False},
    {"name": "greeting_message", "type": "text", "required": False},
    {"name": "search_keywords", "type": "json", "required": False},
    {"name": "screened_reason", "type": "text", "required": False},
    {"name": "first_seen_at", "type": "date", "required": False},
    {"name": "last_seen_at", "type": "date", "required": False},
    {"name": "source_task_id", "type": "text", "required": False},
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
    {"name": "target_action", "type": "text", "required": False},
    {"name": "max_jobs", "type": "number", "required": False},
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


def provision_sqlite_database(
    db_path: str | Path | None = None,
    initial_searches: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Initialize or update PocketBase SQLite schema for required collections.

    If db_path is not specified, it will be automatically resolved from configuration
    (config/settings.local.yaml, config/settings.yaml), env vars (PB_DB_PATH, PB_DATA_DIR),
    or the default fallback path.
    """
    resolved_path = resolve_pocketbase_db_path(db_path, resolve_common_root=True)
    db_file = Path(resolved_path)
    if not db_file.exists():
        logger.warning("Database file %s does not exist, cannot provision schema", db_file)
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
        res_rev_json = json.dumps(RESUME_REVISIONS_FIELDS)
        job_records_json = json.dumps(JOB_RECORDS_FIELDS)
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
                    work_experiences JSON,
                    projects JSON,
                    target_positions JSON,
                    raw_summary TEXT,
                    raw_resume_text TEXT,
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
            # Ensure new columns exist on existing candidate_profiles table
            try:
                cursor.execute("PRAGMA table_info(candidate_profiles)")
                cand_cols = {row[1] for row in cursor.fetchall()}
                for col_name, col_type in [
                    ("work_experiences", "JSON"),
                    ("projects", "JSON"),
                    ("raw_resume_text", "TEXT"),
                ]:
                    if col_name not in cand_cols:
                        cursor.execute(
                            f"ALTER TABLE candidate_profiles ADD COLUMN {col_name} {col_type}"
                        )
            except Exception as e:
                logger.warning("Failed to migrate candidate_profiles table columns: %s", e)

        if "resume_revisions" not in existing:
            cursor.execute(
                """
                INSERT INTO _collections (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
                VALUES ('pbc_res_rev', 0, 'base', 'resume_revisions', ?, '', '', '', '', '')
                """,
                (res_rev_json,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS resume_revisions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    file_name TEXT,
                    file_type TEXT,
                    file_size INTEGER,
                    extracted_text TEXT,
                    diff_summary TEXT,
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
                WHERE name = 'resume_revisions'
                """,
                (res_rev_json,),
            )

        if "job_records" not in existing:
            cursor.execute(
                """
                INSERT INTO _collections (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
                VALUES ('pbc_job_records', 0, 'base', 'job_records', ?, '', '', '', '', '')
                """,
                (job_records_json,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS job_records (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT UNIQUE,
                    title TEXT,
                    company_name TEXT,
                    recruiter_name TEXT,
                    salary_range TEXT,
                    location TEXT,
                    digest TEXT,
                    job_description TEXT,
                    company_scale TEXT,
                    industry TEXT,
                    tags JSON,
                    recruiter_title TEXT,
                    is_headhunter BOOLEAN DEFAULT FALSE,
                    status TEXT DEFAULT 'unmatched',
                    match_score INTEGER,
                    jd_key_requirements JSON,
                    greeting_message TEXT,
                    search_keywords JSON,
                    screened_reason TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    source_task_id TEXT,
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
                WHERE name = 'job_records'
                """,
                (job_records_json,),
            )
            # Ensure new columns exist in existing SQLite table
            cursor.execute("PRAGMA table_info(job_records)")
            job_cols = {row[1] for row in cursor.fetchall()}
            new_job_cols = [
                ("digest", "TEXT"),
                ("company_scale", "TEXT"),
                ("industry", "TEXT"),
                ("tags", "JSON"),
                ("recruiter_title", "TEXT"),
                ("is_headhunter", "BOOLEAN DEFAULT FALSE"),
                ("screened_reason", "TEXT"),
            ]
            for col_name, col_type in new_job_cols:
                if col_name not in job_cols:
                    cursor.execute(f"ALTER TABLE job_records ADD COLUMN {col_name} {col_type}")

            # Backfill and repair legacy job records
            _backfill_legacy_job_records(cursor)

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
        print(
            "❌ Error: 'requests' package is required for remote provisioning. Install via: pip install requests"
        )
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
        resp = session.get(
            f"{base_url}/api/collections", params={"perPage": 200}, timeout=timeout, verify=False
        )
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
                {"name": "work_experiences", "type": "json", "required": False},
                {"name": "projects", "type": "json", "required": False},
                {"name": "target_positions", "type": "json", "required": False},
                {"name": "raw_summary", "type": "text", "required": False},
                {"name": "raw_resume_text", "type": "text", "required": False},
            ],
        },
        {
            "id": "pbc_res_rev",
            "name": "resume_revisions",
            "type": "base",
            "listRule": "",
            "viewRule": "",
            "createRule": "",
            "updateRule": "",
            "deleteRule": "",
            "fields": [
                {"name": "user_id", "type": "text", "required": True},
                {"name": "file_name", "type": "text", "required": True},
                {"name": "file_type", "type": "text", "required": False},
                {"name": "file_size", "type": "number", "required": False},
                {"name": "extracted_text", "type": "text", "required": False},
                {"name": "diff_summary", "type": "text", "required": False},
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
        {
            "id": "pbc_job_records",
            "name": "job_records",
            "type": "base",
            "listRule": "",
            "viewRule": "",
            "createRule": "",
            "updateRule": "",
            "deleteRule": "",
            "fields": [
                {"name": "fingerprint", "type": "text", "required": True},
                {"name": "title", "type": "text", "required": True},
                {"name": "company_name", "type": "text", "required": True},
                {"name": "recruiter_name", "type": "text", "required": True},
                {"name": "salary_range", "type": "text", "required": False},
                {"name": "location", "type": "text", "required": False},
                {"name": "digest", "type": "text", "required": False},
                {"name": "job_description", "type": "text", "required": False},
                {"name": "status", "type": "text", "required": True},
                {"name": "match_score", "type": "number", "required": False},
                {"name": "jd_key_requirements", "type": "json", "required": False},
                {"name": "greeting_message", "type": "text", "required": False},
                {"name": "search_keywords", "type": "json", "required": False},
                {"name": "first_seen_at", "type": "date", "required": False},
                {"name": "last_seen_at", "type": "date", "required": False},
                {"name": "source_task_id", "type": "text", "required": False},
            ],
        },
    ]

    for col in collections_to_create:
        c_name = col["name"]
        if c_name not in existing_names:
            create_resp = session.post(
                f"{base_url}/api/collections", json=col, timeout=timeout, verify=False
            )
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


def _backfill_legacy_job_records(cursor: sqlite3.Cursor) -> None:
    """Migrate and repair existing legacy job records in SQLite database.

    - Strips trailing &@, tags, and punctuation from title
    - Fixes recruiter_name (stripping trailing ·) and moves recruiter titles mistakenly stored in location
    - Re-evaluates is_headhunter based on '猎头' in recruiter_title or recruiter_name
    - Backfills company_scale, industry, tags, and location from jd_key_requirements if missing
    - Backfills digest from job_description if missing
    """
    try:
        # Purge incomplete or partially visible cards where company is missing or 未知公司
        cursor.execute("""
            DELETE FROM job_records
            WHERE company_name IS NULL
               OR TRIM(company_name) = ''
               OR company_name = '未知公司'
        """)

        cursor.execute("""
            SELECT id, title, company_name, recruiter_name, recruiter_title, is_headhunter, 
                   location, digest, job_description, company_scale, industry, tags, 
                   jd_key_requirements 
            FROM job_records
        """)
        rows = cursor.fetchall()
        for row in rows:
            (
                rec_id, title, comp, rec_name, rec_title, is_hh,
                loc, digest, jd, scale, ind, tags_json, reqs_json
            ) = row

            # 1. Clean title
            clean_title = (title or "").strip()
            while True:
                t = re.sub(r"(?:\s*&@\s*|\s*&+\s*|\s*@+\s*)+$", "", clean_title).strip()
                t = re.sub(r"[\s&@]+$", "", t).strip()
                if t == clean_title:
                    break
                clean_title = t

            # 2. Repair recruiter info and location
            rec = (rec_name or "").strip()
            rtitle = (rec_title or "").strip()
            rloc = (loc or "").strip()

            # If location currently holds recruiter title
            if rloc and any(kw in rloc for kw in ("猎头", "顾问", "专员", "专家", "HR", "经理", "总监", "助理", "主管")):
                if not rtitle:
                    rtitle = rloc
                rloc = ""

            # If recruiter name contains "·"
            if any(sep in rec for sep in ("·", "•", "・")):
                parts = [p.strip() for p in re.split(r"[·•・]", rec, maxsplit=1)]
                rec = parts[0].rstrip("·•・").strip()
                if not rtitle and len(parts) > 1 and parts[1]:
                    rtitle = parts[1].strip()
            else:
                rec = rec.rstrip("·•・").strip()

            # If rtitle has trailing city attached
            if rtitle and " " in rtitle:
                sub_toks = rtitle.rsplit(" ", 1)
                if (
                    sub_toks[1] in ("上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京", "苏州", "西安", "海外")
                    or sub_toks[1].endswith("市")
                    or sub_toks[1].endswith("区")
                ):
                    rtitle = sub_toks[0].strip()
                    if not rloc:
                        rloc = sub_toks[1].strip()

            new_is_hh = "猎头" in rtitle or "猎头" in rec

            # 3. Backfill facets from requirements
            new_scale = scale or ""
            new_ind = ind or ""
            new_loc = rloc
            try:
                reqs = json.loads(reqs_json) if isinstance(reqs_json, str) else (reqs_json or [])
            except Exception:
                reqs = []

            try:
                current_tags = json.loads(tags_json) if isinstance(tags_json, str) else (tags_json or [])
            except Exception:
                current_tags = []

            remaining_tags = []
            for r in reqs:
                r_str = str(r).strip()
                if not r_str:
                    continue
                if re.search(r"(\d+[-~至]\d+人|\d+人以上|少于\d+人|\d+人以下)", r_str):
                    if not new_scale:
                        new_scale = r_str
                elif r_str in ("人工智能", "互联网", "互联网/AI", "电子商务", "游戏", "银行", "保险", "医疗健康", "计算机软件"):
                    if not new_ind:
                        new_ind = r_str
                elif (
                    r_str in ("上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京", "苏州", "西安", "海外")
                    or r_str.endswith("市")
                    or r_str.endswith("区")
                ):
                    if not new_loc:
                        new_loc = r_str
                elif r_str != rtitle and r_str != rec and not r_str.startswith("负责"):
                    remaining_tags.append(r_str)

            new_tags = current_tags if current_tags else remaining_tags
            new_digest = digest or jd or ""

            cursor.execute("""
                UPDATE job_records
                SET title = ?, recruiter_name = ?, recruiter_title = ?, is_headhunter = ?,
                    location = ?, digest = ?, company_scale = ?, industry = ?, tags = ?
                WHERE id = ?
            """, (
                clean_title, rec, rtitle, 1 if new_is_hh else 0,
                new_loc, new_digest, new_scale, new_ind, json.dumps(new_tags, ensure_ascii=False),
                rec_id
            ))
    except Exception as ex:
        logger.warning("Error during _backfill_legacy_job_records: %s", ex)


provision_pocketbase_sqlite = provision_sqlite_database


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PocketBase Collection and SQLite Provisioner")
    parser.add_argument(
        "db_path",
        nargs="?",
        default=None,
        help="Path to local data.db SQLite file (defaults to configured pocketbase_db_path)",
    )
    parser.add_argument(
        "--url", help="Remote PocketBase URL, e.g. https://pocketbase.chainer.tech:4433"
    )
    parser.add_argument("--email", help="Superuser / Admin email")
    parser.add_argument("--password", help="Superuser / Admin password")

    args = parser.parse_args()

    if args.url and args.email and args.password:
        print(f"🚀 Provisioning remote PocketBase at {args.url} ...")
        res = provision_remote_pocketbase(args.url, args.email, args.password)
        print(f"Provisioning result: {res}")
        sys.exit(0 if res else 1)
    else:
        target_db = resolve_pocketbase_db_path(args.db_path, resolve_common_root=True)
        print(f"🚀 Provisioning local SQLite database at {target_db} ...")
        res = provision_sqlite_database(target_db)
        print(f"Provisioning status: {res}")
        sys.exit(0 if res else 1)
