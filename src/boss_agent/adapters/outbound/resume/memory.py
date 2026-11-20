"""
boss_agent.adapters.outbound.resume.memory
==========================================
Resume ingestion, text extraction, LLM structured profile generation, and local memory cache.
"""

import json
from pathlib import Path
from typing import Any

from langsmith import traceable
from rich.console import Console

from boss_agent.core.candidate.domain.candidate_profile import (
    CandidateProfile,
    StructuredCandidateProfile,
    stored_profile_is_substantive,
)
from boss_agent.core.candidate.domain.profile_normalizer import ProfileNormalizer
from boss_agent.core.candidate.interfaces.candidate_memory_store import CandidateMemoryStore
from boss_agent.core.candidate.interfaces.resume_text_source import ResumeTextSource
from boss_agent.core.candidate.use_cases.resume_lifecycle import (
    refresh_candidate_profile,
    structure_resume_profile,
)
from boss_agent.core.shared.llm import LLMDecisionClient

console = Console()

# `StructuredCandidateProfile` now lives in the leaf entity module as `CandidateProfile`
# (Issue #383, Spec #382). It is re-exported here as an identity alias because
# __init__.py, twelve test files, and three scripts import it from this module, and
# `screening.py` dispatches on `isinstance(profile, StructuredCandidateProfile)`.
# The alias must stay an identity, not a subclass, or that isinstance check breaks.
# This module's own load/save paths name the entity directly (Issue #385); the alias
# exists for the importers above, not for new code.
__all__ = [
    "CandidateProfile",
    "ProfileNormalizer",
    "ResumeMemoryManager",
    "ResumeTextExtractor",
    "StructuredCandidateProfile",
]


class ResumeTextExtractor(ResumeTextSource):
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
        llm_client: LLMDecisionClient,
        candidate_memory: CandidateMemoryStore | None = None,
        memory_file_path: str | Path | None = None,
        candidate_config_path: str | Path | None = None,
    ):
        if llm_client is None:
            raise ValueError("llm_client must be injected into ResumeMemoryManager")
        self.llm_client = llm_client
        self.candidate_memory = candidate_memory
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
        from boss_agent.adapters.outbound.config import config_realm

        if config_path:
            return config_realm.load_chain([Path(config_path)], config_path=config_path)
        return config_realm.load_chain(list(cls.CANDIDATE_CHAIN))

    def has_memory_file(self) -> bool:
        """Return True if candidate memory profile file exists and is non-empty."""
        return self.memory_path.is_file() and self.memory_path.stat().st_size > 0

    def load_cached_memory(self) -> CandidateProfile | None:
        """Load memory profile from the candidate profile database collection (single source of truth)."""
        # 0. If caller explicitly passed a memory file path and it exists, load it directly
        if self.explicit_memory_file and self.has_memory_file():
            try:
                content = self.memory_path.read_text(encoding="utf-8")
                data = json.loads(content)
                return CandidateProfile.from_dict(data)
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Failed to read explicit memory from {self.memory_path}: {e}[/yellow]"
                )

        # 1. Primary and single source of truth: load from injected candidate memory store
        if self.candidate_memory is None:
            raise ValueError("candidate_memory store must be injected into ResumeMemoryManager")
        from boss_agent.adapters.outbound.pocketbase.async_bridge import run_sync

        data = run_sync(self.candidate_memory.get_candidate_profile(), timeout=5.0)

        if stored_profile_is_substantive(data):
            return CandidateProfile.from_dict(data)
        return None

    @traceable(name="ResumeMemoryManager.generate_and_save_memory", run_type="chain")
    def generate_and_save_memory(self, resume_path: str | Path) -> CandidateProfile:
        """Extract text from resume file, run the unified structuring Use Case, and persist."""
        console.print(f"📄 [bold cyan]Parsing resume file:[/bold cyan] {resume_path}...")
        raw_text = self.extractor.extract_text(resume_path)
        if not raw_text.strip():
            raise ValueError(f"Extracted resume text from {resume_path} was empty.")

        console.print(
            "🧠 [bold cyan]Structuring candidate profile via LLM (unabbreviated)...[/bold cyan]"
        )
        file_name = Path(resume_path).name or "resume.txt"
        sync_to_db = not self.explicit_memory_file
        if sync_to_db:
            if self.candidate_memory is None:
                raise ValueError("candidate_memory store must be injected into ResumeMemoryManager")
            from boss_agent.adapters.outbound.pocketbase.async_bridge import run_sync

            profile = run_sync(
                refresh_candidate_profile(
                    self.llm_client,
                    raw_text,
                    store=self.candidate_memory,
                    file_name=file_name,
                ),
                timeout=10.0,
            )
            console.print(
                "✅ [bold green]Structured candidate profile saved to PocketBase database.[/bold green]"
            )
            return profile

        profile = structure_resume_profile(
            self.llm_client,
            raw_text,
            raise_on_llm_error=True,
        )
        self.save_memory_profile(profile, sync_to_db=False)
        return profile

    def save_memory_profile(
        self, profile: CandidateProfile, sync_to_db: bool | None = None
    ) -> None:
        """Save candidate profile to PocketBase database with local file fallback."""
        if sync_to_db is None:
            sync_to_db = not self.explicit_memory_file

        # 1. Primary: Save to injected candidate memory store
        if sync_to_db:
            if self.candidate_memory is None:
                raise ValueError("candidate_memory store must be injected into ResumeMemoryManager")
            from boss_agent.adapters.outbound.pocketbase.async_bridge import run_sync

            run_sync(
                self.candidate_memory.save_candidate_profile(profile.to_dict()),
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
    ) -> CandidateProfile:
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
