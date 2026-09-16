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

    def _build_system_prompt(self, rules: list[Any] | None = None) -> str:
        """Construct persistent system prompt containing candidate background, anti-template rules, and active style rules."""
        active_rules = [r for r in rules if getattr(r, "enabled", True)] if rules else []
        rules_text = ""
        if active_rules:
            rules_lines = ["\n\n【打招呼个性化长期偏好准则 (用户沉淀的针对性记忆)】："]
            rules_lines.append(
                "以下是求职者长期沉淀的破冰偏好准则。在分析目标岗位 JD 时，若命中触发条件，必须在打招呼文案中贯彻执行对应的策略："
            )
            for idx, r in enumerate(active_rules, 1):
                rules_lines.append(f"{idx}. 【触发条件】: {r.condition} -> 【执行策略】: {r.instruction}")
            rules_text = "\n".join(rules_lines)

        if not self.candidate_profile:
            base = (
                "You are an expert career consultant and job matching specialist. "
                "Analyze job descriptions (JD), extract core requirements, and draft compelling, "
                "tailored greeting messages. Output JSON only."
            )
            return base + rules_text

        return (
            "你是一名资深的技术猎头顾问与求职沟通专家。你当前代表以下求职者进行精准的岗位契合度评估与高回复率打招呼破冰：\n\n"
            f"[求职者背景画像]\n{self.candidate_profile.format_for_prompt()}\n\n"
            "【打招呼破冰铁律与原则】：\n"
            "1. 【严禁模板化套话】：严禁使用“您好！我是XX，有X年经验…”、“看到贵司招聘职位，非常感兴趣…”等空洞模板话术。\n"
            "2. 【深度针对 JD 痛点】：仔细研读目标岗位 JD，提炼出招聘方最核心、最紧迫的 1-2 项技术挑战或业务痛点（例如高并发场景、移动端底层架构、LLM Agent 平台与工作流编排等）。\n"
            "3. 【用匹配成果直接证明能力】：第一句话直接切入该核心痛点，并优先用求职者背景画像中真实存在、最契合的具体项目经历（例如大模型 Agent 架构设计、LangGraph/LangChain 工程化落地、自动化多 Agent 协同、开源项目或大型高并发架构攻坚等）与代表作直接证明匹配能力，严禁仅泛泛罗列基础编程语言，必须突出求职者在 Agent 与核心架构方向上的深度实战积累。\n"
            "4. 【突出为团队带来的价值】：向 HR / 业务面试官展现“我能为该团队/业务解决什么具体问题”。\n"
            "5. 【真诚、专业、精炼】：语气真诚自然、自信得体，字数严格控制在 80-150 字以内，极大降低招聘方阅读与筛选负担，提升沟通回复意愿。\n"
            "6. 【严格 JSON 输出】：严格以标准合法的 JSON 格式输出。字符串内容中严禁出现未转义的英文字符双引号（若需引用或书名请使用中文书名号《》或中文引号“”）。"
            f"{rules_text}"
        )

    @traceable(name="JobMatchGreetingService.evaluate_and_draft_greeting", run_type="chain")
    def evaluate_and_draft_greeting(
        self,
        job: JobPosting,
        profile: StructuredCandidateProfile | None = None,
        rules: list[Any] | None = None,
    ) -> MatchGreetingResult:
        """Evaluate match score and generate personalized greeting message based on JD and profile."""
        if profile:
            self.set_candidate_profile(profile)

        # Strict precondition: job_description must be substantive (> 30 non-whitespace characters)
        jd = (job.job_description or "").strip()
        if len(jd) < 30 or jd in ("无详细岗位描述", "暂无详细描述", "未注明职位"):
            raise ValueError(
                f"Job description is missing or too short ({len(jd)} chars). "
                "Full JD (tv_description) from detail page is strictly required for greeting generation."
            )

        if rules is None:
            from .greeting_rules import load_greeting_rules

            rules = load_greeting_rules()

        system_prompt = self._build_system_prompt(rules=rules)

        user_prompt = (
            "请深入分析以下招聘岗位(JD)，提炼其核心诉求，评估契合度并生成针对该 JD 定制的破冰打招呼文案：\n\n"
            f"职位名称: {job.title}\n"
            f"招聘公司: {job.company_name}\n"
            f"薪资范围: {job.salary_range}\n"
            f"岗位描述(JD):\n{job.job_description or '暂无详细描述'}\n\n"
            "请特别注意：若系统提示中包含【打招呼个性化长期偏好准则】，请在分析该岗位时贯彻对应的策略。\n\n"
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
        rules: list[Any] | None = None,
    ) -> str:
        """Refine and iterate on a greeting draft based on candidate's conversational critique."""
        if profile:
            self.set_candidate_profile(profile)

        if rules is None:
            from .greeting_rules import load_greeting_rules

            rules = load_greeting_rules()

        system_prompt = self._build_system_prompt(rules=rules)
        system_prompt += (
            "\n\n【微调优化特别说明】：\n"
            "求职者对当前招呼语提出了具体的修改建议或批注。你必须充分吸纳求职者的反馈，"
            "重新生成一版契合 JD、满足求职者要求、且符合反套路和精炼原则（80-150字）的破冰打招呼语。\n"
            "请严格以 JSON 格式输出：{\"revised_greeting\": \"重写后的破冰招呼语全文\"}"
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

        try:
            res = self.llm_client.chat_completion_json(messages)
            revised = str(res.get("revised_greeting") or "").strip()
            if revised:
                return revised
        except Exception as e:
            console.print(f"[bold red]❌ LLM greeting refinement error:[/bold red] {e}")

        # Fallback if LLM call failed or returned empty
        return f"{current_greeting} （根据意见优化：{critique}）"

    @traceable(name="JobMatchGreetingService.distill_memory_rule", run_type="chain")
    def distill_memory_rule(
        self,
        job: JobPosting,
        original_greeting: str,
        revised_greeting: str,
        critique: str = "",
    ) -> Any:
        """Distill a generalized condition-action rule from user critique and greeting revision."""
        from .greeting_rules import GreetingStyleRule

        system_prompt = (
            "你是一名资深的求职对话智能体反思与长期记忆沉淀专家。\n"
            "求职者针对特定岗位的初版招呼语提出了微调意见（或进行了手动编辑），系统生成了满意的最终招呼语。\n"
            "你的任务是从这次具体的微调过程中，反思提炼出一条具有通用泛化价值的“场景-策略”长期记忆偏好规则（Condition-Action 风格）。\n\n"
            "【提炼规则】：\n"
            "1. 【触发条件 (condition)】：描述在什么类型的岗位特征、JD要求或技术诉求下该规则应该生效。例如：“当 JD 强调英语能力、外企背景或海外业务时”；“当岗位需要大模型落地与多 Agent 协同架构时”。严禁绑定具体公司名称。\n"
            "2. 【执行策略 (instruction)】：描述打招呼时应采取的话术策略或突出的核心竞争优势。例如：“第一句话点明海外留学经历、英语可作工作语言并主动提及可接受全英文面试”。\n"
            "3. 【严格以 JSON 输出】：\n"
            "{\n"
            '  "condition": "触发条件描述",\n'
            '  "instruction": "执行策略描述"\n'
            "}"
        )

        jd_snippet = (job.job_description or "")[:350]
        user_content = (
            f"目标职位: {job.title}\n"
            f"目标公司: {job.company_name}\n"
            f"岗位描述片段:\n{jd_snippet}\n\n"
            f"修改前招呼语:\n{original_greeting}\n\n"
            f"求职者微调意见:\n{critique or '用户手动修改'}\n\n"
            f"修改后满意招呼语:\n{revised_greeting}\n\n"
            "请反思提炼出一条通用的长期记忆规则（Condition-Action 风格），严格输出 JSON。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        source_job = f"{job.company_name} - {job.title}".strip(" -")
        try:
            data = self.llm_client.chat_completion_json(messages)
            condition = str(data.get("condition") or "").strip()
            instruction = str(data.get("instruction") or "").strip()
            if condition and instruction:
                return GreetingStyleRule(
                    id="",
                    condition=condition,
                    instruction=instruction,
                    enabled=True,
                    source_job=source_job,
                )
        except Exception as e:
            console.print(f"[bold red]❌ LLM rule distillation error:[/bold red] {e}")

        fallback_cond = f"当岗位涉及【{job.title}】相关要求时"
        fallback_inst = critique if critique else f"结合【{job.title}】核心实战成果突出匹配度"
        return GreetingStyleRule(
            id="",
            condition=fallback_cond,
            instruction=fallback_inst,
            enabled=True,
            source_job=source_job,
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
