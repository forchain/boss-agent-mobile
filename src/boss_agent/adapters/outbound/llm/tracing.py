"""
boss_agent.adapters.outbound.llm.tracing
========================================
LangSmith spans for LLM clients and the core job use cases, applied at the adapter
boundary.

Span names and run types are telemetry contracts: dashboards and past traces key on
them, so they are reproduced here verbatim.
"""

from __future__ import annotations

from typing import Any

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from boss_agent.core.job.use_cases.matching import JobMatchGreetingService
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.shared.llm import LLMDecisionClient

__all__ = [
    "TracedCandidateScreener",
    "TracedJobMatchGreetingService",
    "TracedLLMDecisionClient",
    "build_traced_screener",
]


class TracedLLMDecisionClient(LLMDecisionClient):
    """Wrap an LLM client so its calls emit the protocol's named spans.

    The span prefix is the wrapped client's class name, preserving the historical
    ``OpenAIChatClient.chat_completion`` / ``AnthropicChatClient.chat_completion``
    names, and the model/base_url/usage metadata is copied onto the span.
    """

    def __init__(self, inner: LLMDecisionClient, *, span_prefix: str | None = None) -> None:
        self.inner = inner
        self.config = getattr(inner, "config", None)
        prefix = span_prefix or type(inner).__name__
        self._traced_chat = traceable(name=f"{prefix}.chat_completion", run_type="llm")(
            self._chat_completion
        )
        self._traced_chat_json = traceable(name=f"{prefix}.chat_completion_json", run_type="chain")(
            self._chat_completion_json
        )

    def _record_metadata(self) -> None:
        run_tree = get_current_run_tree()
        if run_tree is None:
            return
        if self.config is not None:
            run_tree.metadata["model"] = self.config.model
            run_tree.metadata["base_url"] = self.config.base_url
        usage = getattr(self.inner, "last_usage", None)
        if isinstance(usage, dict):
            run_tree.metadata["usage"] = usage

    def _chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        result = self.inner.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            extra_payload=extra_payload,
        )
        self._record_metadata()
        return result

    def _chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.inner.chat_completion_json(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            extra_payload=extra_payload,
        )
        self._record_metadata()
        return result

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        return self._traced_chat(messages, temperature, max_tokens, response_format, extra_payload)

    def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._traced_chat_json(
            messages, temperature, max_tokens, response_format, extra_payload
        )


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
