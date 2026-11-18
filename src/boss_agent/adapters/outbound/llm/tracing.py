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
from rich.console import Console
from rich.panel import Panel

from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.job.domain.matching import MatchGreetingResult
from boss_agent.core.job.use_cases.matching import JobMatchGreetingService
from boss_agent.core.job.use_cases.screening import CandidateScreener

__all__ = [
    "TracedCandidateScreener",
    "TracedJobMatchGreetingService",
]

console = Console(stderr=True)


class TracedJobMatchGreetingService(JobMatchGreetingService):
    """``JobMatchGreetingService`` with its telemetry spans and Rich match card."""

    @traceable(name="JobMatchGreetingService.evaluate_and_draft_greeting", run_type="chain")
    def evaluate_and_draft_greeting(self, *args, **kwargs):
        return super().evaluate_and_draft_greeting(*args, **kwargs)

    @traceable(name="JobMatchGreetingService.refine_with_critique", run_type="chain")
    def refine_with_critique(self, *args, **kwargs):
        return super().refine_with_critique(*args, **kwargs)

    @traceable(name="JobMatchGreetingService.refine_greeting_prompt", run_type="chain")
    def refine_greeting_prompt(self, *args, **kwargs):
        return super().refine_greeting_prompt(*args, **kwargs)

    def render_match_card(self, job: JobPosting, result: MatchGreetingResult) -> None:
        """Render a formatted Rich card in the console showing match breakdown and greeting text."""
        if result.match_score >= 80:
            score_color = "bold green"
            score_badge = "🔥 高度匹配"
        elif result.match_score >= 60:
            score_color = "bold yellow"
            score_badge = "⚖️ 较为匹配"
        else:
            score_color = "bold red"
            score_badge = "⚠️ 匹配度一般"

        lines = [
            f"[bold]职位:[/bold] {job.title} | [bold]公司:[/bold] {job.company_name} | [bold]薪资:[/bold] {job.salary_range}",
            f"[bold]匹配度评分:[/bold] [{score_color}]{result.match_score}分[/{score_color}] ({score_badge})",
        ]

        if result.jd_key_requirements:
            lines.append("")
            lines.append("[bold cyan]🔍 JD 核心诉求提炼:[/bold cyan]")
            for req in result.jd_key_requirements:
                lines.append(f"  • {req}")

        lines.append("")
        lines.append("[bold cyan]🎯 针对性匹配亮点:[/bold cyan]")
        for reason in result.match_reasons:
            lines.append(f"  • {reason}")

        lines.extend(
            [
                "",
                "[bold cyan]💬 AI 定制破冰招呼语 (结合 JD 痛点，未发送):[/bold cyan]",
                f"[italic white]{result.greeting_message}[/italic white]",
            ]
        )

        console.print(
            Panel(
                "\n".join(lines),
                title="[bold magenta]🤖 AI Job Match & Greeting Analysis[/bold magenta]",
                border_style="cyan",
                expand=False,
            )
        )


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
