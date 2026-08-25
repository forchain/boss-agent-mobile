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
    jd_key_requirements: list[str] = field(default_factory=list)
    greeting_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MatchGreetingResult":
        return cls(
            match_score=int(data.get("match_score") or 50),
            match_reasons=data.get("match_reasons") or [],
            jd_key_requirements=data.get("jd_key_requirements") or [],
            greeting_message=data.get("greeting_message") or "",
        )


class JobMatchGreetingService:
    """Service evaluating job fit and generating tailored greeting message using LLM."""

    def __init__(
        self,
        llm_client: LLMDecisionClient | None = None,
        candidate_profile: StructuredCandidateProfile | None = None,
    ):
        self.llm_client = llm_client or OpenAIChatClient()
        self.candidate_profile = candidate_profile

    def set_candidate_profile(self, profile: StructuredCandidateProfile) -> None:
        """Update candidate memory profile in service context."""
        self.candidate_profile = profile

    def _build_system_prompt(self) -> str:
        """Construct persistent system prompt containing candidate background and anti-template rules."""
        if not self.candidate_profile:
            return (
                "You are an expert career consultant and job matching specialist. "
                "Analyze job descriptions (JD), extract core requirements, and draft compelling, "
                "tailored greeting messages. Output JSON only."
            )

        return (
            "你是一名资深的技术猎头顾问与求职沟通专家。你当前代表以下求职者进行精准的岗位契合度评估与高回复率打招呼破冰：\n\n"
            f"[求职者背景画像]\n{self.candidate_profile.format_for_prompt()}\n\n"
            "【打招呼破冰铁律与原则】：\n"
            "1. 【严禁模板化套话】：严禁使用“您好！我是XX，有X年经验…”、“看到贵司招聘职位，非常感兴趣…”等空洞模板话术。\n"
            "2. 【深度针对 JD 痛点】：仔细研读目标岗位 JD，提炼出招聘方最核心、最紧迫的 1-2 项技术挑战或业务痛点（例如高并发场景、移动端底层架构、LLM Agent 落地等）。\n"
            "3. 【用匹配成果直接证明能力】：第一句话直接切入该核心痛点，并用求职者最契合的项目成果或实战经验证明“我正好具备解决该问题的成熟方案”。\n"
            "4. 【突出为团队带来的价值】：向 HR / 业务面试官展现“我能为该团队/业务解决什么具体问题”。\n"
            "5. 【真诚、专业、精炼】：语气真诚自然、自信得体，字数严格控制在 80-150 字以内，极大降低招聘方阅读与筛选负担，提升沟通回复意愿。\n"
            "6. 【严格 JSON 输出】：严格以标准合法的 JSON 格式输出。字符串内容中严禁出现未转义的英文字符双引号（若需引用或书名请使用中文书名号《》或中文引号“”）。"
        )

    def evaluate_and_draft_greeting(
        self,
        job: JobPosting,
        profile: StructuredCandidateProfile | None = None,
    ) -> MatchGreetingResult:
        """Evaluate match score and generate personalized greeting message based on JD and profile."""
        if profile:
            self.set_candidate_profile(profile)

        system_prompt = self._build_system_prompt()

        user_prompt = (
            "请深入分析以下招聘岗位(JD)，提炼其核心诉求，评估契合度并生成针对该 JD 定制的破冰打招呼文案：\n\n"
            f"职位名称: {job.title}\n"
            f"招聘公司: {job.company_name}\n"
            f"薪资范围: {job.salary_range}\n"
            f"岗位描述(JD):\n{job.job_description or '暂无详细描述'}\n\n"
            "请严格以 JSON 格式输出以下结构：\n"
            "{\n"
            '  "match_score": 匹配度评分(0到100之间的整数),\n'
            '  "jd_key_requirements": [\n'
            '    "从JD提炼的核心诉求/技术挑战1",\n'
            '    "从JD提炼的核心诉求/技术挑战2"\n'
            "  ],\n"
            '  "match_reasons": [\n'
            '    "针对核心诉求1的匹配证明与亮点",\n'
            '    "针对核心诉求2的匹配证明与亮点"\n'
            "  ],\n"
            '  "greeting_message": "针对该JD痛点定制的破冰打招呼文案(80-150字，无模板套话，直击JD诉求)"\n'
            "}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result_data = self.llm_client.chat_completion_json(messages)
            return MatchGreetingResult.from_dict(result_data)
        except Exception as e:
            console.print(f"[bold red]❌ LLM match evaluation error:[/bold red] {e}")
            fallback_msg = (
                f"您好！看到贵公司正在招聘【{job.title}】，我对该方向有深入的实战落地经验，"
                f"希望能与您进一步沟通交流！"
            )
            return MatchGreetingResult(
                match_score=50,
                match_reasons=[f"自动降级生成打招呼 (LLM调用异常: {e})"],
                jd_key_requirements=["岗位要求分析降级"],
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

        content = "\n".join(lines)
        console.print(
            Panel(
                content,
                title="[bold magenta]🤖 AI Job Match & Greeting Analysis[/bold magenta]",
                border_style="cyan",
                expand=False,
            )
        )
