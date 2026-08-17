"""
droid_agent_core.llm
====================
Provider-agnostic LLM reasoning interface for mobile agent decision making.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMConfig:
    provider: str = "openai"
    base_url: str | None = None
    api_key: str | None = None
    model: str = "gpt-4o-mini"
    temperature: float = 0.0


class LLMDecisionClient(ABC):
    """Abstract interface for LLM-driven UI decision making and information parsing."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    def evaluate_text_match(self, candidate_resume: str, job_description: str) -> dict[str, Any]:
        """Evaluate match score between resume and job description."""
