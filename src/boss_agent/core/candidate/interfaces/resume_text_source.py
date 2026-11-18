"""
boss_agent.core.candidate.interfaces.resume_text_source
=======================================================
Port for reading raw resume text; PDF/DOCX parsing libraries are adapter details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ResumeTextSource(ABC):
    """Extracts raw text from a resume file."""

    @abstractmethod
    def extract_text(self, file_path: str | Path) -> str:
        """Return the text content of the resume at ``file_path``."""
