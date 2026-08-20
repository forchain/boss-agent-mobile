"""
boss_agent.matching
===================
Job match evaluation, alignment scoring, customized greeting generation, and Rich console formatting.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from rich.console import Console
from rich.panel import Panel

from droid_agent_core.llm import LLMDecisionClient, OpenAIChatClient

from .memory import StructuredCandidateProfile
from .models import JobPosting

console = Console()


@dataclass
class MatchGreetingResult:
    """Output evaluation combining match score, alignment points, and greeting draft."""

    match_score: int = 50
    match_reasons: list[str] = field(default_factory=list)
    greeting_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MatchGreetingResult":
        return cls(
            match_score=int(data.get("match_score") or 50),
            match_reasons=data.get("match_reasons") or [],
            greeting_message=data.get("greeting_message") or "",
        )


class JobMatchGreetingService:
    """Service evaluating job fit and generating tailored greeting message using LLM."""

    def __init__(self, llm_client: LLMDecisionClient | None = None):
        self.llm_client = llm_client or OpenAIChatClient()

    def evaluate_and_draft_greeting(
        self,
        profile: StructuredCandidateProfile,
        job: JobPosting,
    ) -> MatchGreetingResult:
        """Evaluate match score and generate personalized greeting message based on JD and profile."""
        prompt = (
            "你是一名资深的求职匹配顾问与沟通助手。请根据求职者画像与招聘岗位(JD)，"
            "评估匹配度并生成针对该岗位的个性化打招呼文案。\n\n"
            f"[求职者信息]\n{profile.format_for_prompt()}\n\n"
            f"[招聘岗位]\n"
            f"职位名称: {job.title}\n"
            f"招聘公司: {job.company_name}\n"
            f"薪资范围: {job.salary_range}\n"
            f"岗位描述(JD):\n{job.job_description}\n\n"
            "请严格以 JSON 格式输出以下结构：\n"
            "{\n"
            '  "match_score": 匹配度得分(0到100之间的整数),\n'
            '  "match_reasons": [\n'
            '    "核心匹配点1",\n'
            '    "核心匹配点2"\n'
            "  ],\n"
            '  "greeting_message": "针对该JD的打招呼文案(100-150字，礼貌、专业，直接说明匹配经验与技能亮点，促进招聘方回复)"\n'
            "}"
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a professional career matching and greeting assistant. "
                    "Analyze job requirements precisely and craft compelling, high-conversion greeting messages. "
                    "Output JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            result_data = self.llm_client.chat_completion_json(messages)
            return MatchGreetingResult.from_dict(result_data)
        except Exception as e:
            console.print(f"[bold red]❌ LLM match evaluation error:[/bold red] {e}")
            fallback_msg = (
                f"您好！看到贵公司正在招聘【{job.title}】，我的背景与该岗位高度契合，"
                f"希望能与您进一步沟通交流！"
            )
            return MatchGreetingResult(
                match_score=50,
                match_reasons=[f"自动降级生成打招呼 (LLM调用异常: {e})"],
                greeting_message=fallback_msg,
            )

    def render_match_card(self, job: JobPosting, result: MatchGreetingResult) -> None:
        """Render a formatted Rich card in the console showing match breakdown and greeting text."""
        # Color coding for match score
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
            "",
            "[bold cyan]🎯 匹配核心亮点:[/bold cyan]",
        ]

        for reason in result.match_reasons:
            lines.append(f"  • {reason}")

        lines.extend(
            [
                "",
                "[bold cyan]💬 生成的打招呼草稿 (未发送，供预览):[/bold cyan]",
                f"[italic white]{result.greeting_message}[/italic white]",
            ]
        )

        content = "\n".join(lines)
        console.print(
            Panel(
                content,
                title="[bold magenta]🤖 AI Job Match & Greeting Analysis[/bold magenta]",
                border_style="cyan",
                expand=False,
            )
        )
