"""
tests/unit/test_depth_visit_rule.py
===================================
The Depth Visit Rule and the Job Lifecycle ladder are different questions (#299).

`STATE_RANK` orders a record by how much is *known* about it. That ordering is the right
guard for writes — a later save-only pass must not demote an applied job — and it is the
right satisfaction test for a 深度存JD sweep. It is the wrong answer for 自动打招呼, whose
requirement is a delivered message: comparing ranks there counted a draft (`matched`) as
finished work, and every greeting that never went out was skipped forever after.

Both halves are asserted against both Job Record Store adapters, because the Automation
Worker and the Web Dashboard can be pointed at either and must not disagree.
"""

import pytest
from _job_store_harness import FakePocketBaseSession, pocketbase_job_store

from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.models import (
    STATE_RANK,
    TARGET_ACTION_RANK,
    JobRecordStatus,
    TargetAction,
    depth_already_reached,
)

GOOD_JD = (
    "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
    "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
)


def test_the_visit_rule_reads_the_rank_table_instead_of_bypassing_it():
    """One table states what each depth requires; the predicate just asks it."""
    assert TARGET_ACTION_RANK[TargetAction.SAVE_JD] == STATE_RANK[JobRecordStatus.JD_SAVED]
    assert TARGET_ACTION_RANK[TargetAction.AUTO_APPLY] == STATE_RANK[JobRecordStatus.APPLIED]
    # The trap this removes: `matched` is above enrichment, and below delivery.
    assert STATE_RANK[JobRecordStatus.JD_SAVED] < STATE_RANK[JobRecordStatus.MATCHED]
    assert STATE_RANK[JobRecordStatus.MATCHED] < STATE_RANK[JobRecordStatus.APPLIED]

    draft = {"status": JobRecordStatus.MATCHED.value, "job_description": GOOD_JD}
    applied = {**draft, "status": JobRecordStatus.APPLIED.value}

    assert depth_already_reached(TargetAction.AUTO_APPLY, draft["status"], draft) is False
    assert depth_already_reached(TargetAction.AUTO_APPLY, applied["status"], applied) is True
    assert depth_already_reached(TargetAction.SAVE_JD, draft["status"], draft) is True
    # A save-only run whose record holds no JD still owes it a visit.
    assert (
        depth_already_reached(TargetAction.SAVE_JD, JobRecordStatus.UNMATCHED.value,
                              {"job_description": ""})
        is False
    )
    # A rejection is never "depth reached": it is handled upstream with its own reason.
    assert (
        depth_already_reached(TargetAction.SAVE_JD, JobRecordStatus.IGNORED.value, draft) is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["in_memory", "pocketbase"])
async def test_the_ladders_two_documented_exceptions_survive_the_new_visit_rule(adapter):
    """#299 changes when a page is revisited, never how a status is written.

    The monotonic write guard and its two exceptions — an explicit rejection always wins,
    and a freshly extracted JD lifts a record out of its pre-JD limbo — are asserted here
    for both adapters, so "the ladder is untouched" is evidence rather than a claim.
    """
    store = (
        InMemoryJobRecordStore()
        if adapter == "in_memory"
        else pocketbase_job_store(FakePocketBaseSession())
    )

    async def status_after(fingerprint: str, first: str, then: str) -> str:
        base = {
            "fingerprint": fingerprint,
            "title": "阶梯岗位",
            "company_name": "智元创新",
            "recruiter_name": "王女士",
            "job_description": GOOD_JD,
        }
        await store.upsert_job_record({**base, "status": first})
        await store.upsert_job_record({**base, "status": then})
        rec = await store.get_job_record_by_fingerprint(fingerprint)
        return rec["status"]

    # Exception 1: an explicit rejection wins over anything already recorded.
    assert await status_after("fp-ladder-1", "applied", "ignored") == "ignored"
    assert await status_after("fp-ladder-2", "matched", "ignored") == "ignored"
    # Exception 2: a freshly extracted JD lifts a pre-JD record out of limbo.
    assert await status_after("fp-ladder-3", "unmatched", "jd_saved") == "jd_saved"
    assert await status_after("fp-ladder-4", "digest_only", "jd_saved") == "jd_saved"
    # The rule itself: a later save-only pass cannot demote an applied job.
    assert await status_after("fp-ladder-5", "applied", "matched") == "applied"
    assert await status_after("fp-ladder-6", "applied", "jd_saved") == "applied"
