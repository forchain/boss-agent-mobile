"""
boss_agent.memory
=================
Resume ingestion, text extraction, LLM structured profile generation, and local memory cache.
"""

import json
from pathlib import Path
from typing import Any

from langsmith import traceable
from rich.console import Console

from droid_agent_core.llm import LLMDecisionClient

from .candidate_entities import (
    CandidateProfile,
    StructuredCandidateProfile,
    _extract_education_from_text,
)
from .llm_config import create_llm_client

console = Console()

# `StructuredCandidateProfile` now lives in the leaf entity module as `CandidateProfile`
# (Issue #383, Spec #382). It is re-exported here as an identity alias because
# __init__.py, twelve test files, and three scripts import it from this module, and
# `screening.py` dispatches on `isinstance(profile, StructuredCandidateProfile)`.
# The alias must stay an identity, not a subclass, or that isinstance check breaks.
__all__ = [
    "CandidateProfile",
    "ProfileNormalizer",
    "ResumeMemoryManager",
    "ResumeTextExtractor",
    "StructuredCandidateProfile",
]


class ResumeTextExtractor:
    """Extracts raw text content from local resume files (.pdf, .docx, .txt, .md, .json)."""

    def extract_text(self, file_path: str | Path) -> str:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Resume file not found at: {file_path}")

        ext = path.suffix.lower()
        if ext in [".txt", ".md", ".json"]:
            return self._read_text_file(path)
        elif ext == ".pdf":
            return self._read_pdf(path)
        elif ext in [".docx", ".doc"]:
            return self._read_docx(path)
        else:
            # Fallback to plain text read
            return self._read_text_file(path)

    def _read_text_file(self, path: Path) -> str:
        for encoding in ["utf-8", "gb18030", "gbk", "utf-16"]:
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_bytes().decode("utf-8", errors="replace")

    def _read_pdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages_text = []
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    pages_text.append(extracted)
            return "\n\n".join(pages_text)
        except Exception as e:
            console.print(
                f"[yellow]⚠️  pypdf extraction failed ({e}), falling back to text read[/yellow]"
            )
            return self._read_text_file(path)

    def _read_docx(self, path: Path) -> str:
        try:
            import docx

            doc = docx.Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if row_text:
                        paragraphs.append(" | ".join(row_text))
            return "\n".join(paragraphs)
        except Exception as e:
            console.print(
                f"[yellow]⚠️  python-docx extraction failed ({e}), falling back to text read[/yellow]"
            )
            return self._read_text_file(path)


class ProfileNormalizer:
    """Ensures structured candidate profile data integrity and self-heals missing or null fields."""

    @classmethod
    def normalize(
        cls,
        data: dict[str, Any],
        raw_text: str = "",
    ) -> dict[str, Any]:
        result = dict(data)
        raw_text_clean = (raw_text or result.get("raw_resume_text") or "").strip()

        # 1. Normalize and guarantee profile_document & raw_summary
        doc = (result.get("profile_document") or result.get("raw_summary") or "").strip()
        if (not doc or "#" not in doc) and raw_text_clean:
            doc = (
                f"# 候选人全景画像 (Candidate Profile)\n\n"
                f"## 1. 核心职业定位与背景概览\n{doc or raw_text_clean[:400]}\n\n"
                f"## 2. 核心技术栈与专业能力矩阵\n- 参见原始简历全文\n\n"
                f"## 3. 核心主导项目与技术攻坚 (Key Projects & Architecture)\n{raw_text_clean}\n\n"
                f"## 4. 可量化成果与标志性突破 (Measurable Achievements)\n- 参见经历详情\n\n"
                f"## 5. 资格认证、语言能力与附加信息\n- 详见原始档案"
            )

        result["profile_document"] = doc
        result["raw_summary"] = doc
        result["raw_resume_text"] = raw_text_clean or result.get("raw_resume_text", "")

        # 2. Self-heal target_positions
        positions = result.get("target_positions")
        if not positions or not isinstance(positions, list):
            positions = []
            import re

            search_corpus = f"{doc}\n{raw_text_clean}"
            m = re.search(
                r"(?:求职意向|期望职位|期望岗位|求职岗位|意向岗位)[:：\s]*([^\n，,；;]+)",
                search_corpus,
            )
            if m:
                extracted = m.group(1).strip()
                if extracted and len(extracted) < 30:
                    positions.append(extracted)
            if not positions:
                known_positions = [
                    "AI Agent 架构师",
                    "全栈技术专家",
                    "全栈架构师",
                    "架构师",
                    "技术专家",
                    "算法工程师",
                    "Android 开发",
                    "Python 开发",
                    "前端开发",
                    "后端开发",
                    "全栈开发",
                ]
                for pos in known_positions:
                    if pos in search_corpus and pos not in positions:
                        positions.append(pos)
        result["target_positions"] = [str(p).strip() for p in positions if str(p).strip()]

        # 3. Self-heal name
        name = result.get("name") or ""
        if not name or name in ["求职者", "Candidate", "None"]:
            import re

            m_name = re.search(r"(?:姓名|Name)[:：\s]*([^\s,，;；]+)", raw_text_clean)
            if m_name:
                result["name"] = m_name.group(1).strip()
            else:
                first_line = raw_text_clean.split("\n", 1)[0].strip() if raw_text_clean else ""
                words = first_line.split()
                if (
                    words
                    and 2 <= len(words[0]) <= 4
                    and not any(
                        k in words[0] for k in ["简历", "个人", "电话", "邮箱", "求职", "求职者"]
                    )
                ):
                    result["name"] = words[0]
                else:
                    result["name"] = name or "求职者"
        else:
            result["name"] = name

        # 4. Self-heal years_of_experience
        exp = result.get("years_of_experience")
        if exp is None or (isinstance(exp, int) and exp == 0):
            import re

            m_exp = re.search(
                r"(?<!\d)([1-9]|[1-4]\d)\s*(?:年|years|yrs)(?:研发经验|工作经验|经验)?",
                f"{doc}\n{raw_text_clean}",
                re.IGNORECASE,
            )
            if m_exp:
                result["years_of_experience"] = int(m_exp.group(1))
            else:
                result["years_of_experience"] = 0
        else:
            try:
                result["years_of_experience"] = int(exp)
            except Exception:
                result["years_of_experience"] = 0

        # 5. Self-heal core_skills if empty
        skills = result.get("core_skills")
        if not skills or not isinstance(skills, list):
            skills = []
            known_skills = [
                "Python",
                "FastAPI",
                "Django",
                "Flask",
                "TypeScript",
                "JavaScript",
                "Android",
                "iOS",
                "Unity",
                "Java",
                "Go",
                "Golang",
                "C++",
                "Rust",
                "Vue",
                "React",
                "Svelte",
                "Node.js",
                "Docker",
                "Kubernetes",
                "K8s",
                "LLM",
                "Agent",
                "LangChain",
                "PyTorch",
                "TensorFlow",
                "PostgreSQL",
                "MySQL",
                "Redis",
            ]
            import re

            for s in known_skills:
                if re.search(rf"\b{re.escape(s)}\b", raw_text_clean, re.IGNORECASE):
                    skills.append(s)
            result["core_skills"] = skills

        # 5.5. Self-heal education if empty or missing
        edu = result.get("education")
        if not edu or not isinstance(edu, list):
            search_corpus = f"{doc}\n{raw_text_clean}"
            extracted_edu = cls._extract_education_from_text(search_corpus)
            result["education"] = extracted_edu if extracted_edu else []

        # 6. Guarantee array fields are lists, never None or null
        for array_key in [
            "core_skills",
            "education",
            "work_experiences",
            "projects",
            "project_highlights",
        ]:
            val = result.get(array_key)
            if val is None:
                result[array_key] = []
            elif not isinstance(val, list):
                if isinstance(val, dict):
                    result[array_key] = [f"{k}: {v}" for k, v in val.items()]
                else:
                    result[array_key] = [val]

        return result

    @classmethod
    def _extract_education_from_text(cls, text: str) -> list[dict[str, str]]:
        """Extract structured education entries from resume text or markdown profile document.

        Delegates to the leaf entity module so exactly one implementation exists
        (Issue #383). The extractor was a pure function wearing a classmethod costume
        -- it never used ``cls`` -- so it moved to ``candidate_entities`` where
        ``CandidateProfile.from_dict`` can call it without importing this module.
        This method stays so existing callers keep working.
        """
        return _extract_education_from_text(text)


class ResumeMemoryManager:
    """Manages parsing, structuring, and local caching of candidate profile memory."""

    DEFAULT_MEMORY_PATH = Path("config/candidate_memory.json")

    def __init__(
        self,
        llm_client: LLMDecisionClient | None = None,
        memory_file_path: str | Path | None = None,
        candidate_config_path: str | Path | None = None,
    ):
        self.llm_client = llm_client or create_llm_client()
        self.extractor = ResumeTextExtractor()

        # Load candidate config if available
        self.candidate_config = self._load_candidate_config(candidate_config_path)

        # A memory file counts as explicit only when something really opted into the local
        # JSON file. The candidate chain ships no `memory_path`, and the two values below
        # are the defaults a config may spell out literally — neither is an opt-in, so
        # neither may make the manager read and write the local file over the database
        # profile that #320 made the single source of truth.
        custom_cfg_mem = self.candidate_config.get("memory_path")
        is_custom_file = (
            bool(custom_cfg_mem)
            and str(custom_cfg_mem) != str(self.DEFAULT_MEMORY_PATH)
            and str(custom_cfg_mem) != "config/candidate_memory.json"
        )
        self.explicit_memory_file = (
            memory_file_path is not None
            or (candidate_config_path is not None and bool(custom_cfg_mem))
            or is_custom_file
        )
        configured_memory = (
            memory_file_path
            or (custom_cfg_mem if (candidate_config_path is not None or is_custom_file) else None)
            or self.DEFAULT_MEMORY_PATH
        )
        self.memory_path = Path(configured_memory)
        self.configured_resume_path = self.candidate_config.get("resume_path")

    #: This realm's chain, highest precedence first. `candidate.json`/`candidate.yaml`
    #: are pre-realm spellings kept at the floor so an existing deployment keeps working.
    CANDIDATE_CHAIN: tuple[Path, ...] = (
        Path("config/candidate.local.yaml"),
        Path("config/candidate.local.json"),
        Path("config/candidate.yaml"),
        Path("config/candidate.json"),
    )

    @classmethod
    def _load_candidate_config(cls, config_path: str | Path | None = None) -> dict[str, Any]:
        """The candidate section, through the Configuration Realm's one merge engine.

        This used to be its own loader with first-file-wins semantics and no defaults or
        environment layer — a fourth precedence order for one realm. The realm merges,
        so a local file that sets one key no longer hides the rest of the file beneath it.
        """
        from . import config_realm

        if config_path:
            return config_realm.load_chain([Path(config_path)], config_path=config_path)
        return config_realm.load_chain(list(cls.CANDIDATE_CHAIN))

    def has_memory_file(self) -> bool:
        """Return True if candidate memory profile file exists and is non-empty."""
        return self.memory_path.is_file() and self.memory_path.stat().st_size > 0

    def load_cached_memory(self) -> StructuredCandidateProfile | None:
        """Load memory profile from the candidate profile database collection (single source of truth)."""
        # 0. If caller explicitly passed a memory file path and it exists, load it directly
        if self.explicit_memory_file and self.has_memory_file():
            try:
                content = self.memory_path.read_text(encoding="utf-8")
                data = json.loads(content)
                return StructuredCandidateProfile.from_dict(data)
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Failed to read explicit memory from {self.memory_path}: {e}[/yellow]"
                )

        # 1. Primary and single source of truth: load from database broker
        from boss_agent.async_bridge import run_sync
        from boss_agent.broker import PocketBaseBroker

        broker = PocketBaseBroker()
        data = run_sync(broker.candidate_memory.get_candidate_profile(), timeout=5.0)

        if data and (
            data.get("name")
            or data.get("core_skills")
            or data.get("work_experiences")
            or data.get("projects")
            or data.get("profile_document")
        ):
            return StructuredCandidateProfile.from_dict(data)
        return None

    @traceable(name="ResumeMemoryManager.generate_and_save_memory", run_type="chain")
    def generate_and_save_memory(self, resume_path: str | Path) -> StructuredCandidateProfile:
        """Extract text from resume file, call LLM to parse into unabbreviated schema, and save to database."""
        console.print(f"📄 [bold cyan]Parsing resume file:[/bold cyan] {resume_path}...")
        raw_text = self.extractor.extract_text(resume_path)
        if not raw_text.strip():
            raise ValueError(f"Extracted resume text from {resume_path} was empty.")

        console.print(
            "🧠 [bold cyan]Structuring candidate profile via LLM (unabbreviated)...[/bold cyan]"
        )
        prompt = (
            "请全面、深度、无损地解析以下求职者原始简历文本，并以标准严格的 JSON 格式输出：\n\n"
            f"[简历文本内容]\n{raw_text}\n\n"
            "输出的 JSON 结构规范如下：\n"
            "{\n"
            '  "name": "姓名",\n'
            '  "years_of_experience": 经验年限(整数),\n'
            '  "target_positions": ["期望职位1", "期望职位2"],\n'
            '  "core_skills": ["分类1: 技能列表", "分类2: 技能列表"],\n'
            '  "education": [\n'
            "    {\n"
            '      "school": "学校名称",\n'
            '      "degree": "学历(如: 硕士 / 本科 / 博士)",\n'
            '      "major": "专业名称",\n'
            '      "start_date": "入学年份",\n'
            '      "end_date": "毕业年份"\n'
            "    }\n"
            "  ],\n"
            '  "profile_document": "详尽完整的 Markdown 格式候选人全景画像文档",\n'
            '  "work_experiences": [\n'
            "    {\n"
            '      "company": "公司名称或组织(若无明确公司可填独立开发/开源/业务线/项目名)",\n'
            '      "role": "职位/角色",\n'
            '      "start_date": "起止时间或年份",\n'
            '      "end_date": "结束时间或至今",\n'
            '      "responsibilities": "工作职责与攻坚内容",\n'
            '      "achievements": "量化成果与指标突破"\n'
            "    }\n"
            "  ],\n"
            '  "projects": [\n'
            "    {\n"
            '      "name": "项目名称",\n'
            '      "role": "角色",\n'
            '      "tech_stack": ["技术1", "技术2"],\n'
            '      "description": "项目背景与架构",\n'
            '      "achievements": "指标与产出成果"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "【profile_document 的 Markdown 章节规范（必须极其详尽，100%保留所有项目、架构、技术栈、量化成果与开源链接，严禁做删减概括）】：\n"
            "# 候选人全景画像 (Candidate Profile)\n\n"
            "## 1. 核心职业定位与背景概览\n"
            "(包含姓名、经验年限、学历背景、求职意向与核心定位优势)\n\n"
            "## 2. 核心技术栈与专业能力矩阵\n"
            "(按维度清晰列出 AI Agent/大模型编排、编程语言、后端架构、前端全栈、Web3/量化、云原生与工程化等)\n\n"
            "## 3. 核心主导项目与技术攻坚 (Key Projects & Architecture)\n"
            "(逐个详述各项目/经历的业务背景、系统架构设计、负责模块、攻坚难点、量化表现与开源代码仓库链接)\n\n"
            "## 4. 可量化成果与标志性突破 (Measurable Achievements)\n"
            "(量化指标突破、业务跃升、日活表现、性能突破、行业或社区影响力)\n\n"
            "## 5. 资格认证、语言能力与附加信息\n\n"
            "注意：必须输出标准严格合法的 JSON。所有经历与项目必须在 profile_document 中全量无损展开；字符串内严禁未转义的双引号（若需引用请用中文书名号《》或单引号）。"
        )
        messages = [
            {
                "role": "system",
                "content": "You are a professional HR assistant specializing in parsing candidate resumes into structured JSON with 100% fidelity. Always output valid, complete JSON.",
            },
            {"role": "user", "content": prompt},
        ]

        extra_payload = None
        if (
            "minimax" in getattr(self.llm_client.config, "base_url", "").lower()
            or "minimax" in getattr(self.llm_client.config, "model", "").lower()
        ):
            extra_payload = {"thinking": {"type": "disabled"}}

        result_dict = self.llm_client.chat_completion_json(
            messages,
            max_tokens=16384,
            extra_payload=extra_payload,
        )
        result_dict["raw_resume_text"] = raw_text
        normalized = ProfileNormalizer.normalize(result_dict, raw_text=raw_text)
        profile = StructuredCandidateProfile.from_dict(normalized)

        self.save_memory_profile(profile)
        return profile

    def save_memory_profile(
        self, profile: StructuredCandidateProfile, sync_to_db: bool | None = None
    ) -> None:
        """Save candidate profile to PocketBase database with local file fallback."""
        if sync_to_db is None:
            sync_to_db = not self.explicit_memory_file

        # 1. Primary: Save to PocketBase database (HTTP or SQLite fallback)
        if sync_to_db:
            from boss_agent.async_bridge import run_sync
            from boss_agent.broker import PocketBaseBroker

            broker = PocketBaseBroker()
            run_sync(
                broker.candidate_memory.save_candidate_profile(profile.to_dict()),
                timeout=5.0,
            )
            console.print(
                "✅ [bold green]Structured candidate profile saved to PocketBase database.[/bold green]"
            )

        # 2. Save to local file ONLY if explicit custom memory path is specified
        if self.explicit_memory_file:
            try:
                self.memory_path.parent.mkdir(parents=True, exist_ok=True)
                self.memory_path.write_text(
                    json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

    @traceable(name="ResumeMemoryManager.load_memory", run_type="tool")
    def load_memory(
        self,
        force_refresh: bool = False,
        resume_file: str | Path | None = None,
    ) -> StructuredCandidateProfile:
        """Load candidate profile memory idempotently or regenerate from resume."""
        target_resume = resume_file or self.configured_resume_path

        if not force_refresh and not target_resume:
            cached = self.load_cached_memory()
            if cached is not None:
                console.print(
                    f"💾 [dim]Loaded existing candidate memory from {self.memory_path}[/dim]"
                )
                return cached

        if target_resume and (force_refresh or not self.has_memory_file()):
            return self.generate_and_save_memory(target_resume)

        if not force_refresh:
            cached = self.load_cached_memory()
            if cached is not None:
                console.print(
                    f"💾 [dim]Loaded existing candidate memory from {self.memory_path}[/dim]"
                )
                return cached

        # Look for default resumes directory
        default_resumes_dir = Path("resumes")
        if default_resumes_dir.is_dir():
            candidates = list(default_resumes_dir.glob("*.*"))
            valid_candidates = [
                c
                for c in candidates
                if c.suffix.lower() in [".pdf", ".docx", ".doc", ".txt", ".md"]
            ]
            if valid_candidates:
                return self.generate_and_save_memory(valid_candidates[0])

        cached = self.load_cached_memory()
        if cached is not None:
            return cached

        raise FileNotFoundError(
            f"No candidate memory file found at '{self.memory_path}' and no resume file provided. "
            "Please provide a resume file in config/candidate.local.yaml or via --resume <path>."
        )
