"""
src/boss_agent/broker/provisioner.py
====================================
PocketBase collection provisioner — local SQLite and remote REST.

Both dialects are *views* of :mod:`boss_agent.broker.collection_schema`: the
``CREATE TABLE`` statements and the ``_collections.fields`` JSON are rendered from
the same declarations, so a field cannot exist in one dialect and not the other.
Upgrades render ``ALTER TABLE`` additions and the schema's ordered backfills from
those declarations too, which is why the REST path can now migrate an existing
collection instead of only ever creating new ones.
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# Ensure src/ is in sys.path when executed directly
_src_root = str(Path(__file__).resolve().parent.parent.parent)
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from boss_agent.broker.collection_schema import (  # noqa: E402
    COLLECTIONS,
    JOB_RECORDS_NAME,
    LONG_TEXT_MAX_CHARS,
    SAVED_SEARCHES,
    Collection,
    backfills,
    column_migrations,
    pocketbase_collection_payload,
    pocketbase_fields,
    sqlite_ddl,
    sqlite_index_ddl,
    sqlite_metadata_fields,
    wire_payload,
)
from boss_agent.settings import resolve_pocketbase_db_path  # noqa: E402

# requests and urllib3 are lazily imported in provision_remote_pocketbase

logger = logging.getLogger("boss_agent.broker.provisioner")

#: Retained for callers that pinned the old constant name; the value lives in the
#: schema module because the ``job_description`` cap is a property of the column.
LONG_TEXT_FIELD_MAX_CHARS = LONG_TEXT_MAX_CHARS

#: The fields PocketBase manages itself; the REST dialect must not declare them.
SERVER_MANAGED_FIELDS = frozenset({"id", "created", "updated"})


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


# --------------------------------------------------------------------------- #
# SQLite dialect
# --------------------------------------------------------------------------- #


def _collection_fields_json(collection: Collection) -> str:
    """The ``_collections.fields`` payload, rendered from the schema.

    Distinct from the REST payload on purpose: this JSON *is* PocketBase's view of
    the collection, so it carries the server-managed columns too.
    """
    return json.dumps(sqlite_metadata_fields(collection), ensure_ascii=False)


def _provision_sqlite_collection(
    cursor: sqlite3.Cursor, collection: Collection, *, exists: bool
) -> None:
    """Create or upgrade one collection, both paths rendered from the schema."""
    fields_json = _collection_fields_json(collection)

    if not exists:
        cursor.execute(
            """
            INSERT INTO _collections
                (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
            VALUES (?, 0, 'base', ?, ?, '', '', '', '', '')
            """,
            (collection.collection_id, collection.name, fields_json),
        )
        cursor.execute(sqlite_ddl(collection))
    else:
        cursor.execute(
            """
            UPDATE _collections
            SET fields = ?, listRule = '', viewRule = '', createRule = '', updateRule = '', deleteRule = ''
            WHERE name = ?
            """,
            (fields_json, collection.name),
        )
        cursor.execute(f"PRAGMA table_info({collection.name})")  # noqa: S608 - name is schema-owned
        present = {row[1] for row in cursor.fetchall()}
        for _column, fragment in column_migrations(collection, present):
            cursor.execute(
                f"ALTER TABLE {collection.name} ADD COLUMN {fragment}"  # noqa: S608
            )
        for backfill in backfills(collection, present):
            logger.info("Applying backfill %s on %s", backfill.label, collection.name)
            cursor.execute(backfill.sql)
        repair = _COLLECTION_REPAIRS.get(collection.name)
        if repair is not None:
            repair(cursor)

    for statement in sqlite_index_ddl(collection):
        # An index can fail on legacy data (e.g. a unique index over duplicates that
        # predate it). That must never abort provisioning of the rest of the schema.
        try:
            cursor.execute(statement)
        except sqlite3.Error as ex:
            logger.warning("Could not create index on %s: %s", collection.name, ex)


def _seed_saved_searches(
    cursor: sqlite3.Cursor, initial_searches: dict[str, dict[str, Any]] | None
) -> None:
    """Seed the default searches, with every column taken from the schema."""
    cursor.execute("SELECT COUNT(*) FROM saved_searches")
    if cursor.fetchone()[0]:
        return

    seeds = initial_searches if initial_searches is not None else DEFAULT_INITIAL_SEARCHES
    columns = [
        spec.name for spec in SAVED_SEARCHES.fields if spec.name not in ("created", "updated")
    ]
    placeholders = ", ".join("?" for _ in columns)
    statement = (
        f"INSERT OR IGNORE INTO saved_searches ({', '.join(columns)}) "  # noqa: S608
        f"VALUES ({placeholders})"
    )
    for search_id, item_data in seeds.items():
        payload = wire_payload(SAVED_SEARCHES, item_data)
        payload["id"] = search_id
        values: list[Any] = []
        for name in columns:
            value = payload.get(name)
            if isinstance(value, bool):
                value = 1 if value else 0
            elif isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        cursor.execute(statement, values)
    logger.info("Successfully seeded %d saved_searches into SQLite", len(seeds))


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

        for collection in COLLECTIONS:
            _provision_sqlite_collection(cursor, collection, exists=collection.name in existing)

        _seed_saved_searches(cursor, initial_searches)

        conn.commit()
        return True
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Remote REST dialect
# --------------------------------------------------------------------------- #


def _merge_remote_fields(
    collection: Collection, live_fields: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fold the schema's declared fields into a live collection's field list.

    Declared fields replace live ones of the same name (so a type or option change
    propagates) and are appended when absent. Fields the schema does not know about
    are *kept*: an upgrade must not silently drop a column and its data, and the
    legacy ``assigned_worker`` column is harmless once the lease column is the one
    everything reads.
    """
    declared = {spec["name"]: spec for spec in pocketbase_fields(collection)}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for live in live_fields:
        name = live.get("name")
        if isinstance(name, str) and name in declared:
            merged.append(dict(declared[name]))
            seen.add(name)
        else:
            merged.append(dict(live))
            if isinstance(name, str):
                seen.add(name)
    for name, spec in declared.items():
        if name not in seen:
            merged.append(dict(spec))
    return merged


def _live_field_names(live: dict[str, Any]) -> set[str]:
    return {
        name
        for f in (live.get("fields") or [])
        if isinstance(f, dict) and isinstance(name := f.get("name"), str)
    }


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
    except Exception as ex:
        logger.error("Error fetching collections from %s: %s", pb_url, ex)
        print(f"❌ Network error while querying collections: {ex}")
        return False

    # 3. Create or migrate every collection, from the schema's own description.
    live_by_name = {c.get("name"): c for c in collections_data if isinstance(c, dict)}
    for collection in COLLECTIONS:
        payload = pocketbase_collection_payload(collection)
        live = live_by_name.get(collection.name)
        if live is None:
            create_resp = session.post(
                f"{base_url}/api/collections", json=payload, timeout=timeout, verify=False
            )
            if create_resp.ok:
                logger.info("Created collection '%s' via REST API", collection.name)
                print(f"✨ Created collection '{collection.name}' successfully")
            else:
                logger.error(
                    "Failed to create collection '%s': %s", collection.name, create_resp.text
                )
                print(f"❌ Failed to create collection '{collection.name}': {create_resp.text}")
            continue

        merged = _merge_remote_fields(collection, list(live.get("fields") or []))
        if _live_field_names(live) == {f["name"] for f in merged}:
            print(f"ℹ️ Collection '{collection.name}' already exists")
            continue
        target = live.get("id") or collection.name
        patch_resp = session.patch(
            f"{base_url}/api/collections/{target}",
            json={**live, "fields": merged},
            timeout=timeout,
            verify=False,
        )
        if patch_resp.ok:
            added = sorted({f["name"] for f in merged} - _live_field_names(live))
            logger.info("Migrated collection '%s' (+%s)", collection.name, ", ".join(added) or "none")
            print(f"🔄 Migrated collection '{collection.name}' (+{', '.join(added) or 'none'})")
        else:
            logger.error(
                "Failed to migrate collection '%s': %s", collection.name, patch_resp.text
            )
            print(f"❌ Failed to migrate collection '{collection.name}': {patch_resp.text}")

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
                seeds = initial_searches if initial_searches is not None else DEFAULT_INITIAL_SEARCHES
                for s_id, s_data in seeds.items():
                    record_payload = wire_payload(SAVED_SEARCHES, s_data)
                    record_payload["id"] = s_id
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
    """Repair existing legacy job records in SQLite database.

    - Strips trailing &@, tags, and punctuation from title
    - Fixes recruiter_name (stripping trailing ·) and moves recruiter titles mistakenly stored in location
    - Re-evaluates is_headhunter based on '猎头' in recruiter_title or recruiter_name
    - Backfills company_scale, industry, tags, and location from jd_key_requirements if missing
    - Backfills digest from job_description if missing

    This is row-level *data* repair rather than schema migration, so it stays a
    Python function even though the schema owns the column set it reads and writes.
    """
    try:
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


#: Row-level repairs that run after a collection's columns are in place, keyed by
#: collection name. Schema backfills are SQL; these need per-row interpretation.
_COLLECTION_REPAIRS: Mapping[str, Callable[[sqlite3.Cursor], None]] = {
    JOB_RECORDS_NAME: _backfill_legacy_job_records,
}


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
