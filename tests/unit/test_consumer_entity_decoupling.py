"""
tests/unit/test_consumer_entity_decoupling.py
==============================================
Fast-tier unit tests for the live screening pipeline consuming ``CandidateProfile``
straight from the pure entity module (Issue #384, Spec #382).

``screening`` / ``matching`` / ``feed_pipeline`` / the auto-apply handler are the modules
that actually run during a job search. Each used to reach their profile type through
``memory.py``, which transitively builds an LLM client and touches filesystem caches just
to resolve a class. The entity is a leaf module (stdlib only), so importing the type from
there is what keeps the running pipeline off those dependencies.

The load-bearing risk in this migration is the identity alias: ``_resolve_profile``
dispatches on ``isinstance``. That only stays correct while
``candidate_entities.StructuredCandidateProfile is candidate_entities.CandidateProfile``.
These tests pin both the decoupling and the behavior that depends on it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from boss_agent.candidate_entities import CandidateProfile, StructuredCandidateProfile
from boss_agent.job_entities import JobCardBrief, JobPosting
from boss_agent.screening import CandidateScreener
from boss_agent.screening_policy import ScreeningPolicy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The four consumers this ticket owns. Every one of them used to name ``memory`` in an
# import statement, which is what pulled the LLM client and the filesystem cache onto the
# hot path.
CONSUMER_MODULES = (
    "src/boss_agent/screening.py",
    "src/boss_agent/matching.py",
    "src/boss_agent/feed_pipeline.py",
    "src/boss_agent/worker/handlers/auto_apply.py",
)

GOOD_JD = (
    "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
    "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
)


def _imported_modules(source_file: Path) -> set[str]:
    """Every module named by an import statement, in either absolute or relative form."""
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # ``from .memory import X`` yields module="memory" with level=1, so the
            # relative parts are rejoined onto the package name to make the comparison
            # against a dotted target possible. Level 1 is this package, level 2 its
            # parent, so each level is worth exactly one dot after "boss_agent".
            prefix = "" if not node.level else "boss_agent" + "." * node.level
            if node.module:
                modules.add(f"{prefix}{node.module}")
    return modules


@pytest.mark.parametrize("relative_path", CONSUMER_MODULES)
def test_consumer_does_not_import_memory(relative_path: str) -> None:
    """No consumer reaches its profile type through memory.py any more."""
    source_file = REPO_ROOT / relative_path
    assert source_file.exists(), f"{relative_path} must exist"

    imported = _imported_modules(source_file)
    offenders = {name for name in imported if name.split(".")[-1] == "memory"}

    assert not offenders, (
        f"{relative_path} must import CandidateProfile from candidate_entities/entities, "
        f"not memory — found: {sorted(offenders)}"
    )


@pytest.mark.parametrize("relative_path", CONSUMER_MODULES)
def test_consumer_imports_profile_type_from_entity_module(relative_path: str) -> None:
    """The type has to come from the entity module, or deleting memory would break it."""
    imported = _imported_modules(REPO_ROOT / relative_path)
    from_entity = {
        name for name in imported if name.rsplit(".", 1)[-1] in {"candidate_entities", "entities"}
    }

    assert from_entity, (
        f"{relative_path} must import CandidateProfile from candidate_entities or entities, "
        f"found imports: {sorted(imported)}"
    )


def test_profile_alias_is_an_identity_alias() -> None:
    """The dispatch below is only sound while the two names are the same class.

    A subclass would leave ``isinstance(profile, CandidateProfile)`` False for anything the
    pipeline built through the other name, and ``_resolve_profile`` would quietly return
    ``None`` instead of a profile — failing open on every screen rather than raising.
    """
    assert StructuredCandidateProfile is CandidateProfile


def test_resolve_profile_accepts_candidate_profile() -> None:
    """A profile object passes through the type dispatch untouched."""
    from boss_agent.screening import _resolve_profile

    profile = CandidateProfile(name="李华", core_skills=["Python", "LangGraph"])

    resolved = _resolve_profile(profile)

    assert resolved is profile


def test_resolve_profile_still_accepts_a_dict() -> None:
    """Dict profiles remain supported and must NOT fall through to None.

    This is the fragile line in the migration: it is correct only because the dispatch is
    ``isinstance`` on a real class. If the two names ever diverge, this test is what says
    so instead of a screening run silently screening against no profile at all.
    """
    from boss_agent.screening import _resolve_profile

    resolved = _resolve_profile({"name": "李华", "core_skills": ["Python"]})

    assert isinstance(resolved, CandidateProfile)
    assert resolved is not None
    assert resolved.name == "李华"
    assert "Python" in resolved.core_skills


@pytest.mark.parametrize("empty_input", [None, {}])
def test_resolve_profile_returns_none_for_empty_input(empty_input) -> None:
    """An absent or empty profile resolves to None, as it always has."""
    from boss_agent.screening import _resolve_profile

    assert _resolve_profile(empty_input) is None


def test_candidate_profile_constructs_with_no_arguments() -> None:
    """The auto-apply fallback builds a profile with no arguments at all.

    Every field on the entity carries a default, which is the only reason this degraded
    path constructs successfully. A new required field would turn a graceful fallback into
    a ``TypeError`` at the moment the broker is already failing.
    """
    profile = CandidateProfile()

    assert isinstance(profile, CandidateProfile)
    assert profile.name
    assert profile.years_of_experience == 0
    assert profile.core_skills == []
    assert profile.to_dict()["name"] == profile.name


# --- The four consumers, each handed a profile built from the entity module ---


def test_candidate_screener_accepts_a_candidate_profile() -> None:
    """CandidateScreener screens against an entity-typed profile."""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.chat_completion_json.return_value = {"pass": True, "reason": "技能匹配"}
    screener = CandidateScreener(llm_client=llm)

    result = screener.evaluate_job(
        card=JobCardBrief(
            title="AI Agent 平台工程师",
            company_name="智元创新",
            recruiter_name="周先生 · 技术总监",
            tags=["Python", "LangGraph"],
            digest="负责智能体编排平台研发",
        ),
        jd_text=GOOD_JD,
        profile=CandidateProfile(
            name="李华",
            years_of_experience=6,
            core_skills=["Python", "LangGraph"],
        ),
        policy=ScreeningPolicy(jd_blacklist=["Java"]),
        draft_greeting=False,
    )

    assert result.passed is True


def test_job_match_greeting_service_accepts_a_candidate_profile() -> None:
    """JobMatchGreetingService takes the profile in its constructor and via the setter."""
    from unittest.mock import MagicMock

    from boss_agent.matching import JobMatchGreetingService, offline_match_result

    profile = CandidateProfile(name="李华", years_of_experience=6, core_skills=["Python"])
    service = JobMatchGreetingService(llm_client=MagicMock(), candidate_profile=profile)
    assert service.candidate_profile is profile

    replacement = CandidateProfile(name="王芳", core_skills=["Java"])
    service.set_candidate_profile(replacement)
    assert service.candidate_profile is replacement

    # The offline path is LLM-free, so it exercises the profile type without a client.
    job = JobPosting(
        title="AI Agent 平台工程师",
        company_name="智元创新",
        salary_range="40-60K",
        job_description=GOOD_JD,
    )
    assert offline_match_result(job, profile).match_score > 0


def test_feed_pipeline_config_accepts_a_candidate_profile() -> None:
    """The feed config holds the profile and hands it to the screener unchanged."""
    from boss_agent.feed_pipeline import FeedStreamConfig

    profile = CandidateProfile(name="李华", core_skills=["Python"])

    config = FeedStreamConfig(candidate_profile=profile)

    assert config.candidate_profile is profile


@pytest.mark.asyncio
async def test_auto_apply_resolves_a_candidate_profile_from_the_payload() -> None:
    """The handler builds its profile from the entity when the payload carries one."""
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
    from boss_agent.worker.handlers.auto_apply import AutoApplyHandler

    handler = AutoApplyHandler()

    resolved = await handler._resolve_profile(
        InMemoryTaskBroker(),
        {"candidate_profile": {"name": "李华", "core_skills": ["Python"]}},
    )

    assert isinstance(resolved, CandidateProfile)
    assert resolved.name == "李华"
    assert "Python" in resolved.core_skills


@pytest.mark.asyncio
async def test_auto_apply_falls_back_to_a_zero_argument_profile() -> None:
    """With nothing in the payload or the store, the fallback still constructs.

    This is the branch that runs while the broker is already failing, so it has to keep
    producing a usable profile rather than raising.
    """
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
    from boss_agent.worker.handlers.auto_apply import AutoApplyHandler

    handler = AutoApplyHandler()

    resolved = await handler._resolve_profile(InMemoryTaskBroker(), {})

    assert isinstance(resolved, CandidateProfile)
    assert resolved.years_of_experience == 0
