"""
tests.unit.test_candidate_profile_persistence_contract
=======================================================
The persistence and ingestion seams agree on one profile type (Issue #385, Spec #382).

`CandidateProfile.to_dict()` / `from_dict()` are the single serialization contract.
The stores traffic in dictionaries; the ingestion nodes build the entity. This suite
pins that the two sides map onto each other with no schema loss, and that every store
round trip rebuilds an equal profile.

All Fast Unit: the PocketBase adapter is exercised through the SQLite fallback and a
fake session, so no process and no port is involved.
"""

from pathlib import Path
from typing import Any

import pytest

from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.candidate_entities import CandidateProfile
from boss_agent.candidate_memory_store import (
    PROFILE_JSON_COLUMNS,
    InMemoryCandidateMemoryStore,
    PocketBaseCandidateMemoryStore,
    migrate_legacy_candidate_profile,
)
from boss_agent.graph import resume_normalizer_node, resume_text_extractor_node
from boss_agent.memory import ResumeMemoryManager

#: The eleven keys `CandidateProfile.to_dict()` emits. This is the persisted column
#: contract for `candidate_profiles`; adding, dropping or renaming one is a schema change.
PERSISTED_PROFILE_KEYS = {
    "name",
    "years_of_experience",
    "education",
    "core_skills",
    "work_experiences",
    "projects",
    "project_highlights",
    "target_positions",
    "raw_summary",
    "raw_resume_text",
    "profile_document",
}


def _populated_profile() -> CandidateProfile:
    """A profile that exercises every field, with content on both sides of the round trip.

    `projects` and `project_highlights` are both populated on purpose. `from_dict` keeps
    the two in agreement in both directions, so a hand-built entity with only one of
    them is a state the store never yields; the backfill is pinned by its own test.
    """
    return CandidateProfile(
        name="张三",
        years_of_experience=8,
        education=[{"school": "清华大学", "degree": "硕士", "major": "计算机"}],
        core_skills=["Python", "LangChain", "Kubernetes"],
        work_experiences=[
            {
                "company": "某某科技",
                "role": "后端架构师",
                "start_date": "2020",
                "end_date": "至今",
                "responsibilities": "Agent 编排平台",
                "achievements": "QPS 提升 3 倍",
            }
        ],
        projects=[
            {
                "name": "智能体平台",
                "role": "负责人",
                "tech_stack": ["Python", "LangGraph"],
                "description": "多租户 Agent 编排",
                "achievements": "接入 40 条业务线",
            }
        ],
        project_highlights=[{"name": "智能体平台", "description": "多租户 Agent 编排"}],
        target_positions=["AI Agent 架构师"],
        raw_resume_text="张三，8 年经验，精通 Python 与 LangChain。",
        profile_document="# 候选人全景画像\n\n## 1. 核心职业定位\n资深 AI Agent 架构师",
    )


# --------------------------------------------------------------------------- #
# The serialized contract itself
# --------------------------------------------------------------------------- #


def test_to_dict_emits_exactly_the_persisted_column_contract() -> None:
    """`to_dict()` is the storage contract: eleven keys, and the store persists all of them."""
    emitted = _populated_profile().to_dict()

    assert set(emitted) == PERSISTED_PROFILE_KEYS
    # Every JSON-encoded column the store writes has a counterpart in the entity output.
    assert set(PROFILE_JSON_COLUMNS) <= set(emitted)


def test_a_profile_survives_to_dict_and_from_dict_unchanged() -> None:
    """The serialization contract is lossless: entity -> dict -> entity -> dict is a fixed point."""
    original = _populated_profile()

    restored = CandidateProfile.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()


def test_projects_and_highlights_backfill_each_other() -> None:
    """A profile written with only one of the two project views rebuilds with both.

    Both directions are load-bearing: older rows carry only `project_highlights`,
    newer ones only `projects`. Neither may round-trip to a profile that has lost
    the project it described.
    """
    from_highlights = CandidateProfile.from_dict(
        {"project_highlights": [{"name": "智能体平台", "description": "多租户编排"}]}
    )
    assert [p["name"] for p in from_highlights.projects] == ["智能体平台"]

    from_projects = CandidateProfile.from_dict(
        {"projects": [{"name": "智能体平台", "description": "多租户编排"}]}
    )
    assert from_projects.project_highlights == [{"name": "智能体平台", "description": "多租户编排"}]

    # And the synthesized views are themselves stable under a second round trip.
    assert CandidateProfile.from_dict(from_projects.to_dict()).to_dict() == from_projects.to_dict()


# --------------------------------------------------------------------------- #
# Store adapters
# --------------------------------------------------------------------------- #


def test_in_memory_store_returns_a_dict_the_entity_accepts() -> None:
    """Criterion 1, in-memory adapter: stored dict rebuilds an equal profile."""
    import asyncio

    store = InMemoryCandidateMemoryStore()
    original = _populated_profile()

    asyncio.run(store.save_candidate_profile(original.to_dict()))
    fetched = asyncio.run(store.get_candidate_profile())

    assert fetched is not None
    assert set(fetched) >= PERSISTED_PROFILE_KEYS
    assert CandidateProfile.from_dict(fetched).to_dict() == original.to_dict()


def test_sqlite_fallback_round_trip_loses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1, PocketBase adapter: the offline fallback rebuilds an equal profile.

    The fallback is the only adapter that can be driven end to end without a server.
    It also demonstrates why `raw_summary`'s 4990-char truncation is harmless: the
    store mirrors `profile_document` into it, and `from_dict` reads the document first.
    """
    import asyncio
    import sqlite3

    db_file = tmp_path / "roundtrip.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE candidate_profiles (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, "
        "years_of_experience INTEGER, education TEXT, core_skills TEXT, "
        "project_highlights TEXT, work_experiences TEXT, projects TEXT, "
        "target_positions TEXT, raw_summary TEXT, profile_document TEXT, "
        "raw_resume_text TEXT, updated TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("PB_DB_PATH", str(db_file))

    class _OfflineSession:
        """Every REST call fails, so the adapter takes its documented SQLite path."""

        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("no server in the fast unit tier")

        def post(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("no server in the fast unit tier")

        def patch(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("no server in the fast unit tier")

    store = PocketBaseCandidateMemoryStore(
        base_url="http://127.0.0.1:1",
        session=_OfflineSession(),
        headers=lambda: {},
        sqlite_db_path=db_file,
    )
    original = _populated_profile()

    asyncio.run(store.save_candidate_profile(original.to_dict()))
    fetched = asyncio.run(store.get_candidate_profile())

    assert fetched is not None
    assert CandidateProfile.from_dict(fetched).to_dict() == original.to_dict()


# --------------------------------------------------------------------------- #
# Legacy migration
# --------------------------------------------------------------------------- #


def test_legacy_migration_persists_the_canonical_entity_dict(tmp_path: Path) -> None:
    """Criterion 1, migration: a legacy file is lifted as the canonical eleven-key dict."""
    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text(
        '{"name": "李四", "core_skills": ["Python"], "raw_summary": "# 画像\\n\\n旧文档"}',
        encoding="utf-8",
    )
    broker = InMemoryTaskBroker()

    migrate_legacy_candidate_profile(local_path=legacy_file, broker=broker)

    import asyncio

    fetched = asyncio.run(broker.candidate_memory.get_candidate_profile())
    assert fetched is not None
    # The dict handed to the store is the entity's own serialization, so it must carry
    # all eleven keys even though the legacy file only ever had three.
    assert set(fetched) >= PERSISTED_PROFILE_KEYS
    restored = CandidateProfile.from_dict(fetched)
    assert restored.name == "李四"
    assert restored.core_skills == ["Python"]
    assert restored.profile_document == "# 画像\n\n旧文档"


# --------------------------------------------------------------------------- #
# Ingestion nodes
# --------------------------------------------------------------------------- #


def test_normalizer_node_output_is_a_canonical_profile_dict() -> None:
    """Criterion 2: the ingestion node yields the entity's own serialization."""
    node_output = resume_normalizer_node(
        {
            "raw_resume_text": "王五，5年经验，熟练使用 Python 与 LangChain。",
            "extracted_metadata": {"name": "王五", "years_of_experience": 5},
        }
    )

    normalized = node_output["normalized_profile"]
    assert set(normalized) == PERSISTED_PROFILE_KEYS
    assert "Python" in normalized["core_skills"]

    restored = CandidateProfile.from_dict(normalized)
    assert restored.name == "王五"
    assert restored.years_of_experience == 5
    # The document the node reports and the one inside the dict are the same document.
    assert node_output["profile_document"] == normalized["profile_document"]


def test_text_extractor_node_feeds_the_same_profile_contract(tmp_path: Path) -> None:
    """Criterion 2, file-ingest node: extracted text is what drives the entity's self-healing."""
    resume = tmp_path / "resume.txt"
    resume.write_text("赵六，3年经验。清华大学 硕士 人工智能。", encoding="utf-8")

    node_output = resume_text_extractor_node({"file_path": str(resume)})

    assert node_output["status"] == "text_extracted"
    assert node_output["raw_resume_text"].startswith("赵六")

    normalized = resume_normalizer_node(
        {"raw_resume_text": node_output["raw_resume_text"], "extracted_metadata": {}}
    )["normalized_profile"]
    # No education was supplied; the entity's own extractor self-healed it.
    assert CandidateProfile.from_dict(normalized).education


# --------------------------------------------------------------------------- #
# Manager load / save
# --------------------------------------------------------------------------- #


def test_manager_saves_and_loads_through_the_entity_serialization(tmp_path: Path) -> None:
    """Criterion 3: `ResumeMemoryManager` moves only entity dicts across its boundary."""
    from unittest.mock import AsyncMock, MagicMock, patch

    memory_file = tmp_path / "profile.json"
    store = InMemoryCandidateMemoryStore()
    broker = MagicMock()
    broker.candidate_memory = store
    broker.candidate_memory.get_candidate_profile = AsyncMock(
        side_effect=lambda **kw: store.get_candidate_profile(**kw)
    )
    broker.candidate_memory.save_candidate_profile = AsyncMock(
        side_effect=lambda data, **kw: store.save_candidate_profile(data, **kw)
    )

    manager = ResumeMemoryManager(llm_client=MagicMock(), memory_file_path=memory_file)
    original = _populated_profile()

    with patch("boss_agent.broker.PocketBaseBroker", return_value=broker):
        manager.save_memory_profile(original)

    # The on-disk fallback is written with the same dict the database receives.
    import json

    on_disk = json.loads(memory_file.read_text(encoding="utf-8"))
    assert CandidateProfile.from_dict(on_disk).to_dict() == original.to_dict()

    with patch("boss_agent.broker.PocketBaseBroker", return_value=broker):
        loaded = manager.load_cached_memory()

    assert loaded is not None
    assert isinstance(loaded, CandidateProfile)
    assert loaded.to_dict() == original.to_dict()
