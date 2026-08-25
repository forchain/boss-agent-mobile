"""
boss_agent.memory
=================
Resume ingestion, text extraction, LLM structured profile generation, and local memory cache.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from droid_agent_core.llm import LLMDecisionClient, OpenAIChatClient

console = Console()


@dataclass
class StructuredCandidateProfile:
    """Structured memory profile representing candidate background and key strengths."""

    name: str = "求职者"
    years_of_experience: int = 0
    education: list[dict[str, str]] = field(default_factory=list)
    core_skills: list[str] = field(default_factory=list)
    project_highlights: list[dict[str, str]] = field(default_factory=list)
    target_positions: list[str] = field(default_factory=list)
    raw_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StructuredCandidateProfile":
        raw_skills = data.get("core_skills") or []
        normalized_skills: list[str] = []
        if isinstance(raw_skills, dict):
            for k, v in raw_skills.items():
                if isinstance(v, list):
                    normalized_skills.append(f"{k}: {', '.join(str(x) for x in v)}")
                else:
                    normalized_skills.append(f"{k}: {v}")
        elif isinstance(raw_skills, list):
            for item in raw_skills:
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, list):
                            normalized_skills.append(f"{k}: {', '.join(str(x) for x in v)}")
                        else:
                            normalized_skills.append(f"{k}: {v}")
                else:
                    normalized_skills.append(str(item))

        return cls(
            name=data.get("name") or "求职者",
            years_of_experience=int(data.get("years_of_experience") or 0),
            education=data.get("education") or [],
            core_skills=normalized_skills,
            project_highlights=data.get("project_highlights") or [],
            target_positions=data.get("target_positions") or [],
            raw_summary=data.get("raw_summary") or "",
        )

    def format_for_prompt(self) -> str:
        """Format the profile into a clean summary block for LLM prompts."""
        edu_str = "; ".join(
            f"{e.get('school', '')} ({e.get('degree', '')} - {e.get('major', '')})"
            for e in self.education
        )
        skills_str = ", ".join(self.core_skills)
        targets_str = ", ".join(self.target_positions)
        projects_str = "\n".join(
            f"- {p.get('name', '')}: {p.get('description', '')}" for p in self.project_highlights
        )

        return (
            f"姓名: {self.name}\n"
            f"工作经验: {self.years_of_experience}年\n"
            f"教育背景: {edu_str or '未注明'}\n"
            f"核心技能: {skills_str or '未注明'}\n"
            f"期望职位: {targets_str or '未注明'}\n"
            f"项目经验:\n{projects_str or '未注明'}\n"
            f"个人总结: {self.raw_summary or '未注明'}"
        )


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


class ResumeMemoryManager:
    """Manages parsing, structuring, and local caching of candidate profile memory."""

    DEFAULT_MEMORY_PATH = Path("config/candidate_memory.json")

    def __init__(
        self,
        llm_client: LLMDecisionClient | None = None,
        memory_file_path: str | Path | None = None,
        candidate_config_path: str | Path | None = None,
    ):
        self.llm_client = llm_client or OpenAIChatClient()
        self.extractor = ResumeTextExtractor()

        # Load candidate config if available
        self.candidate_config = self._load_candidate_config(candidate_config_path)

        configured_memory = (
            memory_file_path or self.candidate_config.get("memory_path") or self.DEFAULT_MEMORY_PATH
        )
        self.memory_path = Path(configured_memory)
        self.configured_resume_path = self.candidate_config.get("resume_path")

    @staticmethod
    def _load_candidate_config(config_path: str | Path | None = None) -> dict[str, Any]:
        search_paths: list[Path] = []
        if config_path:
            search_paths.append(Path(config_path))
        else:
            search_paths.extend(
                [
                    Path("config/candidate.local.yaml"),
                    Path("config/candidate.local.json"),
                    Path("config/candidate.yaml"),
                    Path("config/candidate.json"),
                ]
            )

        for p in search_paths:
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8")
                    if p.suffix in [".yaml", ".yml"]:
                        loaded = yaml.safe_load(content) or {}
                    else:
                        loaded = json.loads(content) or {}
                    if isinstance(loaded, dict):
                        return loaded
                except Exception:
                    pass
        return {}

    def has_memory_file(self) -> bool:
        """Return True if candidate memory profile file exists and is non-empty."""
        return self.memory_path.is_file() and self.memory_path.stat().st_size > 0

    def load_cached_memory(self) -> StructuredCandidateProfile | None:
        """Load memory profile from disk if available."""
        if not self.has_memory_file():
            return None
        try:
            content = self.memory_path.read_text(encoding="utf-8")
            data = json.loads(content)
            return StructuredCandidateProfile.from_dict(data)
        except Exception as e:
            console.print(
                f"[yellow]⚠️  Failed to read cached memory from {self.memory_path}: {e}[/yellow]"
            )
            return None

    def generate_and_save_memory(self, resume_path: str | Path) -> StructuredCandidateProfile:
        """Extract text from resume file, call LLM to parse into schema, and save to memory file."""
        console.print(f"📄 [bold cyan]Parsing resume file:[/bold cyan] {resume_path}...")
        raw_text = self.extractor.extract_text(resume_path)
        if not raw_text.strip():
            raise ValueError(f"Extracted resume text from {resume_path} was empty.")

        console.print("🧠 [bold cyan]Structuring candidate profile via LLM...[/bold cyan]")
        prompt = (
            "请全面、完整地解析以下求职者原始简历文本，并提取出便于 Agent 理解的完整结构化个人信息：\n\n"
            f"[简历文本内容]\n{raw_text}\n\n"
            "请严格以 JSON 格式输出以下结构，完整提取并保留所有核心技能分类、所有项目经历与个人总结：\n"
            "{\n"
            '  "name": "姓名",\n'
            '  "years_of_experience": 经验年限(整数),\n'
            '  "education": [{"school": "学校", "degree": "学历", "major": "专业"}],\n'
            '  "core_skills": ["分类1: 技能列表", "分类2: 技能列表"],\n'
            '  "project_highlights": [{"name": "项目名称", "description": "项目成果与亮点描述（完整提取所有项目经历）"}],\n'
            '  "target_positions": ["期望职位1", "期望职位2"],\n'
            '  "raw_summary": "全面总结的核心个人背景亮点与技术优势概述"\n'
            "}\n\n"
            "注意：必须输出标准严格合法的 JSON。core_skills 数组中的每个元素必须为字符串（如 \"AI与智能体: Claude, Langchain\"），严禁在数组内部直接书写 \"key\": value 键值对；字符串内严禁未转义的双引号（若需引用请用中文书名号《》或单引号）。"
        )
        messages = [
            {
                "role": "system",
                "content": "You are a professional HR assistant specializing in parsing candidate resumes into structured JSON. Always output valid, complete JSON.",
            },
            {"role": "user", "content": prompt},
        ]

        result_dict = self.llm_client.chat_completion_json(messages)
        profile = StructuredCandidateProfile.from_dict(result_dict)

        # Save to local memory file
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(
            f"✅ [bold green]Structured memory profile saved to:[/bold green] {self.memory_path}"
        )
        return profile

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
