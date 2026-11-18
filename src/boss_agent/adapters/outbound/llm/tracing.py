"""
boss_agent.adapters.outbound.llm.tracing
========================================
LangSmith spans for the core job use cases, applied at the adapter boundary.

The span names and run types are telemetry contracts: dashboards and past traces key on
them, so they are reproduced here verbatim even though the decorated methods themselves
now live in framework-free core. Compose these classes instead of the core ones wherever
a run should be traced.
"""

from __future__ import annotations

from langsmith import traceable

from boss_agent.core.job.use_cases.matching import JobMatchGreetingService
from boss_agent.core.job.use_cases.screening import CandidateScreener
from droid_agent_core.llm import LLMDecisionClient

__all__ = [
    "TracedCandidateScreener",
    "TracedJobMatchGreetingService",
    "build_traced_screener",
]


class TracedJobMatchGreetingService(JobMatchGreetingService):
    """``JobMatchGreetingService`` with its telemetry spans applied at the edge."""

    @traceable(name="JobMatchGreetingService.evaluate_and_draft_greeting", run_type="chain")
    def evaluate_and_draft_greeting(self, *args, **kwargs):
        return super().evaluate_and_draft_greeting(*args, **kwargs)

    @traceable(name="JobMatchGreetingService.refine_with_critique", run_type="chain")
    def refine_with_critique(self, *args, **kwargs):
        return super().refine_with_critique(*args, **kwargs)

    @traceable(name="JobMatchGreetingService.refine_greeting_prompt", run_type="chain")
    def refine_greeting_prompt(self, *args, **kwargs):
        return super().refine_greeting_prompt(*args, **kwargs)


class TracedCandidateScreener(CandidateScreener):
    """``CandidateScreener`` with the screening-stage spans applied at the edge."""

    @traceable(name="CandidateScreener.evaluate_card", run_type="chain")
    def evaluate_card(self, *args, **kwargs):
        return super().evaluate_card(*args, **kwargs)

    @traceable(name="CandidateScreener.evaluate_job", run_type="chain")
    def evaluate_job(self, *args, **kwargs):
        return super().evaluate_job(*args, **kwargs)

    @traceable(name="CandidateScreener.screen_jd_semantics", run_type="chain")
    def _screen_jd_semantics(self, *args, **kwargs):
        return super()._screen_jd_semantics(*args, **kwargs)

    @traceable(name="CandidateScreener.retest_with_critique", run_type="chain")
    def retest_with_critique(self, *args, **kwargs):
        return super().retest_with_critique(*args, **kwargs)

    @traceable(name="CandidateScreener.evaluate_jd", run_type="chain")
    def evaluate_jd(self, *args, **kwargs):
        return super().evaluate_jd(*args, **kwargs)

    _evaluate_jd = evaluate_jd

    @traceable(name="CandidateScreener.refine_screening_prompt", run_type="chain")
    def refine_screening_prompt(self, *args, **kwargs):
        return super().refine_screening_prompt(*args, **kwargs)

    def _greeting_service(self) -> JobMatchGreetingService:
        """Lazily build the traced greeting service so save-only runs never load config."""
        if self._matching_service is None:
            self._matching_service = TracedJobMatchGreetingService(
                llm_client=self._llm_client(), greeting_prompt=self.greeting_prompt
            )
        return self._matching_service


def build_traced_screener(
    *,
    llm_client: LLMDecisionClient,
    matching_service: JobMatchGreetingService | None = None,
    greeting_prompt: str | None = None,
    screening_prompt: str | None = None,
) -> TracedCandidateScreener:
    """Build a traced candidate screener from already-resolved dependencies and prompts."""
    return TracedCandidateScreener(
        llm_client=llm_client,
        matching_service=matching_service,
        greeting_prompt=greeting_prompt,
        screening_prompt=screening_prompt,
    )
