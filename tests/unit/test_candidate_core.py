"""
tests.unit.test_candidate_core
==============================
Seams introduced when the Candidate aggregate moved into ``core`` (Spec #443, ticket #451).

The resume lifecycle used to be five LangGraph node bodies that each reached for their
own concrete dependency (the pypdf/docx extractor, the LLM factory, the PocketBase
broker). The nodes now delegate to framework-free callables that receive those
dependencies from their caller, and file extraction sits behind a port. These tests pin
the callables' observable behavior with injected doubles.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from boss_agent.adapters.memory.candidate_memory_store import InMemoryCandidateMemoryStore
from boss_agent.adapters.outbound.resume.memory import ResumeTextExtractor
from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.candidate.interfaces.resume_text_source import ResumeTextSource
from boss_agent.core.candidate.use_cases.resume_lifecycle import (
    analyze_profile_diff,
    extract_resume_text,
    generate_profile_document,
    normalize_extracted_profile,
    persist_resume_profile,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class _FakeResumeTextSource(ResumeTextSource):
    """A ResumeTextSource that never touches a file, so the seam itself is observable."""

    def __init__(self, text: str = "FAKE RESUME TEXT") -> None:
        self.text = text
        self.requested_paths: list[str] = []

    def extract_text(self, file_path: str | Path) -> str:
        self.requested_paths.append(str(file_path))
        return self.text


def test_resume_text_extractor_satisfies_the_resume_text_source_port(tmp_path: Path) -> None:
    """The pypdf/docx implementation stays an adapter detail behind the core port."""
    extractor = ResumeTextExtractor()
    resume = tmp_path / "resume.txt"
    resume.write_text("姓名: 李四", encoding="utf-8")

    assert isinstance(extractor, ResumeTextSource)
    assert "李四" in extractor.extract_text(resume)


def test_extract_resume_text_delegates_to_the_injected_source() -> None:
    """The extraction step receives its file reader; it constructs no concrete one."""
    source = _FakeResumeTextSource()

    result = extract_resume_text(
        source,
        file_path="resumes/candidate.pdf",
        raw_resume_text="",
    )

    assert result["raw_resume_text"] == "FAKE RESUME TEXT"
    assert result["file_name"] == "candidate.pdf"
    assert result["status"] == "text_extracted"
    assert source.requested_paths == ["resumes/candidate.pdf"]


def test_extract_resume_text_keeps_provided_text_without_calling_the_source() -> None:
    """Raw text already in hand short-circuits extraction, as the graph node always did."""
    source = _FakeResumeTextSource()

    result = extract_resume_text(source, raw_resume_text="inline text", file_name="inline.txt")

    assert result["raw_resume_text"] == "inline text"
    assert result["file_name"] == "inline.txt"
    assert source.requested_paths == []


def test_normalize_extracted_profile_returns_the_canonical_profile_dict() -> None:
    """Normalization stays the entity's own serialization, self-healed."""
    result = normalize_extracted_profile(
        {"name": "王五", "years_of_experience": 5},
        raw_resume_text="王五，5年经验，熟练使用 Python 与 LangChain。",
    )

    normalized = result["normalized_profile"]
    assert result["status"] == "normalized"
    assert CandidateProfile.from_dict(normalized).name == "王五"
    assert "Python" in normalized["core_skills"]
    assert result["profile_document"] == normalized["profile_document"]


def test_analyze_profile_diff_reports_an_initial_ingest() -> None:
    """No existing profile is the initial-merge decision, with its stable summary."""
    result = analyze_profile_diff(None, {"name": "李四"}, merge_mode="")

    assert result["existing_profile"] is None
    assert "【初次录入】" in result["diff_summary"]
    assert result["merge_mode"] == "initial"
    assert result["status"] == "diff_ready"


def test_analyze_profile_diff_reports_merge_changes() -> None:
    """Merge mode compares against the stored profile and keeps the requested mode."""
    existing = {
        "name": "李四",
        "years_of_experience": 7,
        "core_skills": ["Python"],
        "target_positions": ["Python后端开发"],
        "raw_summary": "旧版",
    }

    result = analyze_profile_diff(
        existing,
        {
            "name": "李四",
            "years_of_experience": 8,
            "core_skills": ["Python", "FastAPI"],
            "target_positions": ["AI Agent 架构师"],
        },
        merge_mode="merge",
    )

    assert result["existing_profile"] is existing
    assert result["merge_mode"] == "merge"
    assert "经验年限: 7年 -> 8年" in result["diff_summary"]
    assert "新增期望职位" in result["diff_summary"]


def test_persist_resume_profile_merges_and_records_a_revision() -> None:
    """Persistence is the merge decision plus the injected store's two writes."""
    store = InMemoryCandidateMemoryStore(
        initial_profiles={
            "default": {
                "name": "李四",
                "years_of_experience": 7,
                "core_skills": ["Python", "Django"],
                "target_positions": ["Python后端开发"],
                "profile_document": "旧版画像",
            }
        }
    )

    result = asyncio.run(
        persist_resume_profile(
            store,
            user_id="default",
            normalized_profile={
                "name": "李四",
                "years_of_experience": 8,
                "core_skills": ["Python", "FastAPI"],
                "target_positions": ["AI Agent 架构师"],
                "profile_document": "新版画像",
                "raw_resume_text": "raw",
            },
            existing_profile={
                "name": "李四",
                "years_of_experience": 7,
                "core_skills": ["Python", "Django"],
                "target_positions": ["Python后端开发"],
                "profile_document": "旧版画像",
            },
            merge_mode="merge",
            file_name="resume_v2.md",
            raw_resume_text="raw",
            diff_summary="+ 新增 FastAPI",
        )
    )

    assert result["status"] == "completed"
    saved = result["final_profile"]
    assert saved["years_of_experience"] == 8
    assert saved["core_skills"] == ["Python", "Django", "FastAPI"]
    assert saved["target_positions"] == ["Python后端开发", "AI Agent 架构师"]
    assert saved["profile_document"] == "新版画像"

    revision = result["revision_record"]
    assert revision["file_name"] == "resume_v2.md"
    assert revision["file_type"] == "md"
    assert revision["file_size"] == len("raw")
    assert revision["diff_summary"] == "+ 新增 FastAPI"


def test_generate_profile_document_falls_back_when_the_llm_fails() -> None:
    """A failed structured call still yields the raw-text fallback document."""

    class _FailingClient:
        def chat_completion_json(self, *args, **kwargs):
            raise RuntimeError("rate limit")

    result = generate_profile_document(_FailingClient(), "王五，10年经验")

    assert result["status"] == "document_generated"
    assert result["extracted_metadata"] == {"raw_summary": "王五，10年经验"}
    assert result["profile_document"] == "王五，10年经验"


def test_core_candidate_imports_nothing_from_adapters_or_frameworks() -> None:
    """The aggregate is framework-free: stdlib plus the core shared kernel only."""
    package_root = REPO_ROOT / "src/boss_agent/core/candidate"
    allowed_roots = {
        "__future__",
        "abc",
        "collections",
        "pathlib",
        "re",
        "typing",
        "unicodedata",
        "dataclasses",
    }

    offenders: dict[str, set[str]] = {}
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        package = "boss_agent.core.candidate"
        if path.parent != package_root:
            package = f"{package}.{path.parent.name}"
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    drop = node.level - 1
                    anchor = parts[: len(parts) - drop] if drop else parts
                    imported.add(".".join([*anchor, node.module] if node.module else anchor))
                elif node.module:
                    imported.add(node.module)
        bad = {
            name
            for name in imported
            if name.split(".")[0] not in allowed_roots
            and not name.startswith("boss_agent.core")
            and name != "boss_agent.core"
        }
        if bad:
            offenders[str(path.relative_to(package_root))] = bad

    assert offenders == {}, f"core/candidate imported non-core modules: {offenders}"
