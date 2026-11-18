"""
boss_agent.feed_ledger
======================
The Feed Verdict & Ledger module (spec #444, ticket #454).

It owns every Job Record Store write a feed run makes, the Job Lifecycle State
transition implicit in each verdict, the greeting-provenance precedence, the Daily
Greeting Limit read and the direct-hire exclusion pool query. It has no Appium import
and no page-object reference, so quota degradation, provenance and cool-down are
testable without a device; ``JobFeedPipeline`` keeps only navigation and orchestration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .enums import JobRecordStatus, ScreeningStage
from .errors import TransportError
from .identifier_helpers import is_direct_hire_company
from .job_store import JobRecordStore
from .keyword_constants import GREETING_SOURCE_AGENT
from .screening import JobEvaluationResult, JobVerdictStage

LogSink = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class PersistedVerdict:
    """One stored verdict, plus the values the run reports about it."""

    record: dict[str, Any]
    greeting_message: str
    match_score: int


class FeedVerdictLedger:
    """The one writer of Job Record Store state during a feed run."""

    def __init__(self, store: JobRecordStore, log: LogSink) -> None:
        self.store = store
        self._log = log

    async def find_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        return await self.store.get_job_record_by_fingerprint(fingerprint)

    async def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        return await self.store.upsert_job_record(dict(record)) or {}

    async def clear_communication(self, record_id: Any) -> None:
        await self.store.clear_job_communication(record_id)

    async def excluded_direct_companies(self, cooldown_days: int | None) -> set[str]:
        return await self.store.get_applied_direct_companies(cooldown_days=cooldown_days)

    async def applied_today(self) -> int:
        """Read the day's applied count once; callers reuse the value on the run."""
        try:
            return await self.store.count_today_applied_jobs()
        except TransportError as e:
            await self._log(f"⚠️ [持久化降级] 今日投递额度查询遇到持久化异常（{e}），无法确认配额")
            raise

    @staticmethod
    def quota_exhausted(applied_today: int, daily_limit: int) -> bool:
        """Whether the Daily Greeting Limit leaves no room for another dispatch."""
        return applied_today >= daily_limit

    @staticmethod
    def greeting_source(payload: dict[str, Any], evaluation: JobEvaluationResult) -> str:
        """Greeting Provenance precedence: what the run states, then the draft it made."""
        return payload.get("greeting_source") or (
            GREETING_SOURCE_AGENT if evaluation.greeting_message else ""
        )

    @staticmethod
    def applies_enterprise_exclusion(record: dict[str, Any]) -> bool:
        """Whether a dispatched greeting anchors the employer in the same-company pool."""
        company_name = (record.get("company_name") or "").strip()
        return bool(company_name) and is_direct_hire_company(
            company_name, record.get("is_headhunter")
        )

    async def persist_verdict(
        self,
        payload: dict[str, Any],
        status: JobRecordStatus,
        evaluation: JobEvaluationResult,
    ) -> PersistedVerdict:
        """Write one terminal verdict, applying the greeting and score provenance rules."""
        greeting_message = payload.get("greeting_message") or evaluation.greeting_message
        greeting_source = self.greeting_source(payload, evaluation)
        # A run that reused a human copy asked no model to score the posting, so
        # ``evaluation.match_score`` is the dataclass default — 0. Zero is not "unknown",
        # it is "the worst score possible": writing it deletes the number the operator used
        # to approve that copy, so the key is left out and the stored value stands.
        match_score = evaluation.match_score or payload.get("match_score")
        screened_reason = payload.get("screened_reason") or (
            evaluation.reason if status is JobRecordStatus.IGNORED else ""
        )
        screening_stage = payload.get("screening_stage") or (
            ScreeningStage.DEEP_SCREENER.value
            if status is JobRecordStatus.IGNORED
            and getattr(evaluation, "stage", None) is JobVerdictStage.FILTERED_BY_DEEP_SCREENER
            else ""
        )
        body = {
            **payload,
            "status": status.value,
            "greeting_message": greeting_message,
            "greeting_source": greeting_source,
            "screened_reason": screened_reason,
            "screening_stage": screening_stage,
            "jd_key_requirements": evaluation.jd_key_requirements
            or payload.get("jd_key_requirements", []),
        }
        if match_score is not None:
            body["match_score"] = match_score
        try:
            saved = await self.store.upsert_job_record(dict(body)) or {}
        except TransportError as e:
            await self._log(f"⚠️ [持久化降级] 岗位记录写入遇到持久化异常（{e}）")
            saved = dict(body)
        if match_score is None:
            match_score = saved.get("match_score")
        return PersistedVerdict(
            record=saved,
            greeting_message=greeting_message,
            match_score=int(match_score or 0),
        )
