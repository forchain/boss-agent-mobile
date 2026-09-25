"""
boss_agent.matching
===================
Job match evaluation, alignment scoring, customized greeting generation, and Rich console formatting.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from langsmith import traceable
from rich.console import Console
from rich.panel import Panel

from droid_agent_core.llm import LLMDecisionClient, OpenAIChatClient

from .greeting_prompt import load_greeting_prompt
from .memory import StructuredCandidateProfile
from .models import JobPosting, ScreeningPolicy, is_substantive_jd

console = Console(stderr=True)


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

    def _build_system_prompt(self, greeting_prompt: str | None = None) -> str:
        """Compose the system prompt: structural scaffolding plus the settled
        Greeting Prompt document embedded verbatim (ADR 0010). The editable
        persona and writing principles live in the document; code keeps only
        the candidate profile interpolation and the JSON output contract."""
        if greeting_prompt is None:
            greeting_prompt = load_greeting_prompt()

        if not self.candidate_profile:
            base = (
                "You are an expert career consultant and job matching specialist. "
                "Analyze job descriptions (JD), extract core requirements, and draft compelling, "
                "tailored greeting messages. Output JSON only."
            )
            return (
                f"{base}\n\n"
                "【打招呼长期记忆提示词 (Greeting Prompt — 求职者沉淀的最终写作准则，必须逐条贯彻)】：\n"
                f"{greeting_prompt}"
            )

        return (
            "你当前代表以下求职者进行精准的岗位契合度评估与高回复率打招呼破冰：\n\n"
            f"[求职者背景画像]\n{self.candidate_profile.format_for_prompt()}\n\n"
            "【打招呼长期记忆提示词 (Greeting Prompt — 求职者沉淀的最终写作准则，必须逐条贯彻)】：\n"
            f"{greeting_prompt}\n\n"
            "【输出格式硬性约定】：\n"
            "【严格 JSON 输出】：严格以标准合法的 JSON 格式输出。字符串内容中严禁出现未转义的英文字符双引号（若需引用或书名请使用中文书名号《》或中文引号“”）。"
        )

    @staticmethod
    def _build_blacklist_constraint_section(screening_policy: ScreeningPolicy | None) -> str:
        """Render the dynamic negative-constraint block injected from the active ScreeningPolicy."""
        if not screening_policy or not screening_policy.enable_screening:
            return ""
        tokens = list(
            dict.fromkeys(
                t.strip()
                for t in (screening_policy.jd_blacklist + screening_policy.title_blacklist)
                if t and t.strip()
            )
        )
        if not tokens:
            return ""
        return (
            "\n\n【黑名单消极约束（动态注入自当前筛选策略 ScreeningPolicy）】：\n"
            f"以下关键词/主题是求职者明确拉黑的负面清单：{tokens}\n"
            "1. 严禁在匹配论证与破冰文案中迎合、夸赞或主动罗列上述黑名单主题；"
            "不得将求职者经历中与之相关的部分包装成匹配亮点。\n"
            "2. 若目标岗位的核心职责恰以黑名单主题为主体（例如挂名Agent平台实为纯Java开发），"
            "应判定为实质不匹配：显著调低 match_score，保持中性表述，避免任何赞美话术。\n"
        )

    @traceable(name="JobMatchGreetingService.evaluate_and_draft_greeting", run_type="chain")
    def evaluate_and_draft_greeting(
        self,
        job: JobPosting,
        profile: StructuredCandidateProfile | None = None,
        greeting_prompt: str | None = None,
        screening_policy: ScreeningPolicy | None = None,
    ) -> MatchGreetingResult:
        """Evaluate match score and generate personalized greeting message based on JD and profile.

        The active ScreeningPolicy blacklists are dynamically injected as negative
        disqualification constraints so the draft never pitches or praises disallowed stacks.
        """
        if profile:
            self.set_candidate_profile(profile)

        # Strict precondition: job_description must be substantive (> 30 non-whitespace characters)
        jd = (job.job_description or "").strip()
        if not is_substantive_jd(jd):
            raise ValueError(
                f"Job description is missing or too short ({len(jd)} chars). "
                "Full JD (tv_description) from detail page is strictly required for greeting generation."
            )

        system_prompt = self._build_system_prompt(greeting_prompt=greeting_prompt)
        system_prompt += self._build_blacklist_constraint_section(screening_policy)

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

    @traceable(name="JobMatchGreetingService.refine_with_critique", run_type="chain")
    def refine_with_critique(
        self,
        job: JobPosting,
        current_greeting: str,
        critique: str,
        history: list[dict[str, str]] | None = None,
        profile: StructuredCandidateProfile | None = None,
        greeting_prompt: str | None = None,
    ) -> str:
        """Refine and iterate on a greeting draft based on candidate's conversational critique."""
        if profile:
            self.set_candidate_profile(profile)

        system_prompt = self._build_system_prompt(greeting_prompt=greeting_prompt)
        system_prompt += (
            "\n\n【微调优化特别说明】：\n"
            "求职者对当前招呼语提出了具体的修改建议或批注。你必须充分吸纳求职者的反馈，"
            "重新生成一版契合 JD、满足求职者要求、且符合反套路和精炼原则（80-150字）的破冰打招呼语。\n"
            '请严格以 JSON 格式输出：{"revised_greeting": "重写后的破冰招呼语全文"}'
        )

        user_content = (
            f"职位名称: {job.title}\n"
            f"招聘公司: {job.company_name}\n"
            f"薪资范围: {job.salary_range}\n"
            f"岗位描述(JD):\n{job.job_description or '暂无详细描述'}\n\n"
            f"【当前打招呼语】:\n{current_greeting}\n\n"
            f"【求职者微调修改意见】:\n{critique}\n\n"
            "请根据上述修改意见重新生成破冰打招呼文案，严格以 JSON 格式输出：\n"
            '{\n  "revised_greeting": "修改后的招呼语全文"\n}'
        )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for turn in history:
                if isinstance(turn, dict) and "role" in turn and "content" in turn:
                    messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_content})

        llm_error: Exception | None = None
        try:
            res = self.llm_client.chat_completion_json(messages)
            revised = str(res.get("revised_greeting") or "").strip()
            if revised:
                return revised
            llm_error = ValueError("LLM returned empty revised_greeting")
        except Exception as e:
            llm_error = e

        # LLM call failed or returned empty. Surface the failure honestly so
        # the caller (Python script / API endpoint) can show a real error to
        # the UI — never silently produce a concatenated "fake refined"
        # greeting that the user would mistake for an actual LLM rewrite.
        #
        # Important: log to stderr (not stdout) so the rich-formatted error
        # does not corrupt the JSON contract that the caller expects on
        # stdout. console.print defaults to stdout which causes the API
        # endpoint's JSON parser to fail with "Bad control character".
        import sys

        sys.stderr.write(f"❌ LLM greeting refinement error: {llm_error}\n")
        sys.stderr.flush()

        raise RuntimeError(f"LLM 微调失败，无法生成优化文案：{llm_error}")

    @traceable(name="JobMatchGreetingService.refine_greeting_prompt", run_type="chain")
    def refine_greeting_prompt(
        self,
        job: JobPosting,
        original_greeting: str,
        revised_greeting: str,
        critique: str = "",
        current_prompt: str | None = None,
    ) -> str:
        """Rewrite the entire Greeting Prompt in light of one concrete job example.

        The editable document stays coherent by whole-document rewrite: every
        still-valid point must be preserved, the new lesson is generalized into
        the prose. Never fabricates — failures raise so the caller can surface
        a real error (ADR 0010).
        """
        if current_prompt is None:
            current_prompt = load_greeting_prompt()

        system_prompt = (
            "你是一名资深的求职对话智能体提示词打磨专家 (Prompt Refinement Specialist)。\n"
            "下面给出求职者当前沉淀的《打招呼长期记忆提示词》(Greeting Prompt)，以及一次具体的招呼语改进实例：初版招呼语 →（求职者批注）→ 最终满意的招呼语。\n"
            "你的任务：结合该实例反思，整篇重写这份 Greeting Prompt，使其成为更准确、更通用、连贯可读的最终写作准则。\n\n"
            "【改写规则】：\n"
            "1. 【保留全部既有条款 — 最重要】：必须保留当前提示词中所有仍然有效的写作要点，严禁在重写中丢失历史沉淀；只做修正、合并、去重与泛化。\n"
            "2. 【通用化】：把本次实例中的具体经验改写为适用于所有同类岗位的通用写作准则，严禁绑定具体公司名称或单一职位。\n"
            "3. 【理解并转化批注意图】：求职者的批注可能口语化、零散，必须真正理解其意图并转化为可执行的写作指令，而不是逐字照搬原话。\n"
            "4. 【保持既有 Markdown 散文结构】：延续当前文档的人设句与【打招呼破冰铁律与原则】编号条目风格，输出仍然是一份可直接使用的完整提示词。\n"
            '5. 【严格 JSON 输出】：{"prompt": "改进后的完整提示词全文"}'
        )

        jd_snippet = (job.job_description or "")[:350]
        user_content = (
            f"【当前 Greeting Prompt 全文】：\n{current_prompt}\n\n"
            f"目标职位: {job.title}\n"
            f"目标公司: {job.company_name}\n"
            f"岗位描述片段:\n{jd_snippet}\n\n"
            f"【本次实例】\n修改前招呼语:\n{original_greeting}\n\n"
            f"求职者批注:\n{critique or '用户手动修改'}\n\n"
            f"修改后满意招呼语:\n{revised_greeting}\n\n"
            '请结合上述实例整篇重写 Greeting Prompt，严格输出 JSON：{"prompt": "改进后的完整提示词全文"}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            data = self.llm_client.chat_completion_json(messages)
            prompt_text = str(data.get("prompt") or "").strip()
            if not prompt_text:
                raise ValueError("LLM 返回空 prompt 字段")
            return prompt_text
        except Exception as e:
            # stderr only: the caller parses a JSON contract from stdout.
            import sys

            sys.stderr.write(f"❌ Greeting Prompt refinement error: {e}\n")
            raise RuntimeError(f"提示词打磨失败，无法生成改进版 Greeting Prompt：{e}") from e

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
