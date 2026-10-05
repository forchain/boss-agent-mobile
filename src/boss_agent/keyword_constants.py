"""
boss_agent.keyword_constants
============================
Keyword constants, regex patterns, and badge dictionaries for the Boss Application Layer (Issue #311, Spec #303).
"""

from __future__ import annotations

import re

#: Characters Boss uses between a recruiter's name and the title it appends.
RECRUITER_SEPARATORS: tuple[str, ...] = ("·", "•", "・")
_RECRUITER_SEPARATOR_RE: re.Pattern[str] = re.compile(r"[·•・]")

#: Platform chrome that appears as its own text node or tag on a job card. Declared once
#: so the card parser, the locator read and the tag sanitizer cannot disagree about
#: which strings are badges rather than content.
PLATFORM_BADGE_MARKERS: frozenset[str] = frozenset({"猎", "新", "急", "热", "置顶"})

COMMON_COMPOUND_SURNAMES: frozenset[str] = frozenset(
    {
        "欧阳",
        "太史",
        "端木",
        "上官",
        "司马",
        "东方",
        "独孤",
        "南宫",
        "万俟",
        "闻人",
        "夏侯",
        "诸葛",
        "尉迟",
        "公羊",
        "赫连",
        "澹台",
        "皇甫",
        "宗政",
        "濮阳",
        "公冶",
        "太叔",
        "申屠",
        "公孙",
        "慕容",
        "仲孙",
        "钟离",
        "长孙",
        "宇文",
        "司徒",
        "司空",
        "司寇",
        "子车",
        "微生",
        "呼延",
        "拓跋",
        "乐正",
        "壤驷",
        "公良",
        "漆雕",
        "巫马",
        "公西",
        "令狐",
    }
)

GENERIC_ROLE_TOKENS: frozenset[str] = frozenset(
    {
        "hr",
        "hrbp",
        "猎头",
        "猎头顾问",
        "招聘者",
        "招聘",
        "招聘顾问",
        "招聘专员",
        "招聘经理",
        "人事",
        "人事经理",
        "顾问",
        "面试官",
        "管理员",
        "工作人员",
        "合伙人",
        "团队",
        "部门",
    }
)

KNOWN_CITIES: tuple[str, ...] = (
    "上海",
    "北京",
    "深圳",
    "广州",
    "杭州",
    "成都",
    "武汉",
    "南京",
    "苏州",
    "西安",
    "重庆",
    "天津",
    "长沙",
    "厦门",
    "合肥",
    "青岛",
    "郑州",
    "大连",
    "海外",
    "远程",
)

RECRUITER_TITLE_KEYWORDS: tuple[str, ...] = (
    "猎头",
    "顾问",
    "专员",
    "专家",
    "HR",
    "招聘",
    "经理",
    "主管",
    "总监",
    "助理",
    "VP",
    "合伙人",
    "Recruiter",
    "Leader",
    "HRBP",
    "负责人",
    "人事",
    "招聘者",
)

EDUCATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "本科",
        "硕士",
        "大专",
        "博士",
        "学历不限",
        "初中及以下",
        "中专/中技",
        "高中",
        "大专及以上",
        "本科及以上",
        "硕士及以上",
        "MBA/EMBA",
    }
)

EXPERIENCE_KEYWORDS: frozenset[str] = frozenset(
    {
        "经验不限",
        "应届生",
        "在校生",
        "应届毕业生",
    }
)

COMPANY_INDICATOR_KEYWORDS: tuple[str, ...] = (
    "公司",
    "科技",
    "网络",
    "集团",
    "企业",
    "银行",
    "证券",
    "基金",
    "保险",
    "有限",
    "工作室",
    "事务所",
    "中心",
    "信息",
    "数据",
    "通信",
    "智能",
    "工业",
    "制造",
    "电商",
    "商贸",
    "游戏",
    "互娱",
    "软件",
    "技术",
    "医疗",
    "健康",
    "生物",
    "制药",
    "教育",
    "文化",
    "传媒",
)

INVALID_COMPANY_NAMES: frozenset[str] = frozenset(
    {"", "未知公司", "未注明公司", "null", "undefined"}
)

_CN_JD_HEADER_WORDS = "岗位职责|工作职责|职位描述|任职要求|任职资格|加分项|基本要求|必须要求|关于我们|公司介绍|加分条件|薪酬福利"
_EN_JD_HEADER_WORDS = (
    r"role summary|role overview|job summary|job description|responsibilit(?:y|ies)|"
    r"requirement(?:s)?|qualification(?:s)?|preferred (?:qualifications|requirements)|"
    r"skills|about (?:us|the (?:company|team|role|job))|benefits|compensation|perks"
)
_JD_HEADER_ANNOTATION = r"(?:\s*[【\[(（][^】\]）]*[】\]）])?"
_JD_HEADER_STANDALONE_RE = re.compile(
    rf"^\s*[【\[(（]?\s*(?:{_CN_JD_HEADER_WORDS}|{_EN_JD_HEADER_WORDS})\s*[】\]）]?"
    rf"{_JD_HEADER_ANNOTATION}\s*[:：]?\s*[；;，,。.]?\s*$",
    re.IGNORECASE,
)
_JD_HEADER_PREFIX_RE = re.compile(
    rf"^[【\[(（]\s*(?:{_CN_JD_HEADER_WORDS}|{_EN_JD_HEADER_WORDS})\s*[】\]）]{_JD_HEADER_ANNOTATION}?"
    rf"\s*[:：]?\s*[；;，,。.]?\s*",
    re.IGNORECASE,
)

COMMON_TECH_TAGS: tuple[str, ...] = (
    "Java",
    "Python",
    "Go",
    "Golang",
    "Rust",
    "C++",
    "C#",
    ".NET",
    "PHP",
    "React Native",
    "React",
    "Flutter",
    "Vue",
    "Angular",
    "Node.js",
    "TypeScript",
    "JavaScript",
    "Android",
    "iOS",
    "鸿蒙",
    "HarmonyOS",
    "小程序",
    "RN",
    "LLM",
    "AI",
    "大模型",
    "Agent",
    "Prompt",
    "RAG",
    "AIGC",
    "NLP",
    "CV",
    "机器学习",
    "深度学习",
    "Spring",
    "SpringBoot",
    "FastAPI",
    "Django",
    "Flask",
    "MySQL",
    "PostgreSQL",
    "Redis",
    "MongoDB",
    "Elasticsearch",
    "Kafka",
    "Kubernetes",
    "K8s",
    "Docker",
    "DevOps",
    "CI/CD",
    "全栈",
    "架构师",
    "前端",
    "后端",
    "移动端",
    "测开",
    "运维",
    "微服务",
    "3-5年",
    "5-10年",
    "1-3年",
    "10年以上",
    "应届生",
    "本科",
    "硕士",
    "博士",
    "大专",
)

COMMUNICATED_BUTTON_TEXTS: tuple[str, ...] = ("继续沟通",)
CLOSED_BUTTON_TEXTS: tuple[str, ...] = ("停止招聘", "职位已关闭", "已下线")
UNCONTACTED_BUTTON_TEXTS: tuple[str, ...] = ("立即沟通", "聊一聊", "去沟通", "发消息")

# Provenance of an `applied` record: agent-dispatched greeting vs. pre-existing platform contact.
APPLIED_SOURCE_AGENT: str = "agent_auto_send"
APPLIED_SOURCE_PLATFORM_HISTORICAL: str = "platform_historical"

#: Provenance of the greeting text on a Job Record (issue #300). This is what decides
#: whether a run may spend tokens re-drafting it: an operator who previews a greeting does
#: so by generating or editing it in the Web Dashboard, and once that copy exists the
#: agent sends it verbatim instead of writing a new one over it on every sweep.
#:
#: A record written before this field existed carries neither value, and
#: :func:`greeting_is_human` reads that as "not human" — the safe direction, because the
#: cost of guessing wrong is a regenerated draft, not a message nobody approved.
GREETING_SOURCE_AGENT: str = "agent_draft"
GREETING_SOURCE_HUMAN: str = "human"

EXPIRED_POSTING_REASON: str = "岗位已失效/停止招聘"

# Telemetry shared by both detail-inspecting handlers when a headhunter posting is
# spared the bottom commute probe (ticket #255). Kept in one place so the two paths
# cannot drift into reporting the skip differently.
HEADHUNTER_COMMUTE_PROBE_SKIP_REASON: str = (
    "猎头岗位（企业信息保密），跳过底部通勤距离探测以节省耗时"
)

# The other way a probe is declined: the posting is in no 考察名单 district, so its exact
# distance was never something the operator asked to have measured (spec #328). Reporting
# the headhunter reason here would blame the channel for a decision the district list made.
NOT_INSPECTED_DISTRICT_SKIP_REASON: str = "商圈不在考察名单，默认距离满足并跳过底部通勤距离探测"


DEFAULT_COMMUNICATION_COOLDOWN_DAYS: int = 30

# A JD shorter than this carries no evaluable signal: screening or greeting from it would
# produce a meaningless score and a fabricated greeting. The card screener and the greeting
# service gate on the same precondition, so it is stated once here.
MIN_JD_CHARS: int = 30
UNUSABLE_JD_MARKERS: tuple[str, ...] = ("无详细岗位描述", "暂无详细描述", "未注明职位")

#: The markers that mean a job description was never fully expanded. One declaration,
#: read by the detail page before it spends scrolls and taps on `查看更多`
#: (`pages.JobDetailPage.expand_description_if_collapsed`) and by the feed pipeline before
#: it decides a stored JD is as good as a fresh read (issue #301). Two owners for this rule
#: is how a stored body and a live body could stop meaning the same thing.
TRUNCATED_JD_MARKERS: tuple[str, ...] = ("查看更多", "展开")

#: Unambiguous staffing/agency markers. Deliberately excludes broad industry
#: words such as 咨询 or 科技, which real employers also carry (e.g. 埃森哲咨询).
HEADHUNTER_AGENCY_KEYWORDS: tuple[str, ...] = (
    "人力资源",
    "人力资本",
    "劳务派遣",
    "劳务服务",
    "人才服务",
    "人才中介",
    "人才咨询",
    "人才招聘",
    "招聘服务",
    "职业介绍",
    "企业管理咨询",
    "猎头",
    "代招",
)
