"""
boss_agent.candidate_entities
==============================
Candidate profile domain entity (Issue #311, Spec #303).

This module is a leaf: standard library only, no LLM framework, no LangSmith
decorator, no filesystem cache, no network. Every helper it needs lives beside
the entity so the profile can be built, serialized, and rendered in-process.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


def _extract_education_from_text(text: str) -> list[dict[str, str]]:
    """Extract structured education entries from resume text or markdown profile document.

    This is the single implementation of the heuristic. It lives here, in the leaf
    entity module, rather than on ``ProfileNormalizer`` in ``memory`` so that
    ``CandidateProfile.from_dict`` can self-heal education without importing anything
    that pulls in an LLM client -- importing it from ``memory`` would create a
    ``candidate_entities -> memory -> candidate_entities`` cycle. It uses no ``cls``,
    so the classmethod it used to be was a costume over a pure function.
    """
    norm_text = unicodedata.normalize("NFKC", text or "")
    edu_list: list[dict[str, str]] = []
    degree_words = [
        "硕士",
        "本科",
        "博士",
        "学士",
        "大专",
        "MBA",
        "EMBA",
        "PhD",
        "Master",
        "Bachelor",
    ]

    # 1. Pipe-separated format: School | Degree | Major (with optional date or details)
    # e.g.: - 沙迦美国大学（American University of Sharjah）| 硕士 | 计算机工程（2019 - 2022）
    pipe_pattern = re.compile(r"[-*•]?\s*([^|\n\r]+?)\s*\|\s*([^|\n\r]+?)\s*\|\s*([^\n\r|]+)")
    for m in pipe_pattern.finditer(norm_text):
        parts = [m.group(1).lstrip("-*• ").strip(), m.group(2).strip(), m.group(3).strip()]
        degree = ""
        for p in parts:
            if any(d in p for d in degree_words):
                degree = p
                break
        if degree:
            rem = [p for p in parts if p != degree]
            school = rem[0] if rem else ""
            major = rem[1] if len(rem) > 1 else ""
            school = re.sub(r"^[#\s\-*•]+", "", school).strip()
            if school and len(school) < 60:
                edu_list.append({"school": school, "degree": degree, "major": major})

    # 2. Explicit narrative: e.g. "硕士毕业于沙迦美国大学计算机工程专业"
    if not edu_list:
        narrative_pattern = re.compile(
            r"(?:(硕士|博士|本科|学士|研究生))?\s*毕业于\s*([^\s，,。；;]+?(?:大学|学院|分校|Institute|University|College))\s*([^\s，,。；;]+?专业)?",
            re.IGNORECASE,
        )
        for m in narrative_pattern.finditer(norm_text):
            deg = m.group(1) or "本科"
            sch = m.group(2).strip()
            maj = (m.group(3) or "").replace("专业", "").strip()
            if sch:
                edu_list.append({"school": sch, "degree": deg, "major": maj})

    # 3. Space/slash-delimited format: School Degree Major
    # e.g.: "清华大学 硕士 人工智能"
    if not edu_list:
        space_pattern = re.compile(
            r"[-*•]?\s*([^\s，,。；;|]+?(?:大学|学院|分校|Institute|University|College))\s+([^\s，,。；;|]*?(?:硕士|本科|学士|博士|大专|MBA|PhD|Master|Bachelor)[^\s，,。；;|]*)\s+([^\n\r，,。；;|]+)"
        )
        for m in space_pattern.finditer(norm_text):
            sch = re.sub(r"^[#\s\-*•]+", "", m.group(1)).strip()
            deg = m.group(2).strip()
            maj = m.group(3).strip()
            if sch and len(sch) < 60:
                edu_list.append({"school": sch, "degree": deg, "major": maj})

    # Deduplicate by base school name while preserving the richest entry
    deduped: list[dict[str, str]] = []
    seen_schools: set[str] = set()
    for e in edu_list:
        base_school = re.sub(r"[\(（].*?[\)）]", "", e["school"]).strip()
        if base_school and base_school not in seen_schools:
            seen_schools.add(base_school)
            deduped.append(e)

    return deduped


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    """Coerce an untrusted collection field into a list of dicts.

    ``from_dict`` is fed raw LLM output and directly-supplied profile dicts, so the
    collection fields arrive as ``None``, a bare mapping, a scalar, or a list holding
    non-dict entries. Every downstream reader (``format_for_prompt``) calls
    ``.get()`` on these entries, so anything that is not a dict is dropped rather than
    allowed to raise. A bare mapping is treated as a single entry.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_str_list(value: Any) -> list[str]:
    """Coerce an untrusted collection field into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    return []


def _coerce_years(value: Any) -> int:
    """Read years of experience, degrading to 0 rather than raising on junk input."""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass
class CandidateProfile:
    """Structured memory profile representing candidate background and key strengths."""

    name: str = "求职者"
    years_of_experience: int = 0
    education: list[dict[str, str]] = field(default_factory=list)
    core_skills: list[str] = field(default_factory=list)
    work_experiences: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    project_highlights: list[dict[str, str]] = field(default_factory=list)
    target_positions: list[str] = field(default_factory=list)
    raw_summary: str = ""
    raw_resume_text: str = ""
    profile_document: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        doc = (self.profile_document or self.raw_summary or "").strip()
        d["profile_document"] = doc
        d["raw_summary"] = doc
        if d.get("projects") is None:
            d["projects"] = []
        if d.get("work_experiences") is None:
            d["work_experiences"] = []
        if d.get("project_highlights") is None:
            d["project_highlights"] = []
        if d.get("target_positions") is None:
            d["target_positions"] = []
        if d.get("core_skills") is None:
            d["core_skills"] = []
        if d.get("education") is None:
            d["education"] = []
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateProfile:
        if not isinstance(data, dict):
            data = {}

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
        else:
            normalized_skills = [str(raw_skills)]

        raw_work = _as_dict_list(data.get("work_experiences"))
        raw_projects = _as_dict_list(data.get("projects"))
        raw_highlights = _as_dict_list(data.get("project_highlights"))
        if not raw_projects and raw_highlights:
            raw_projects = [
                {
                    "name": p.get("name", ""),
                    "description": p.get("description", ""),
                    "role": p.get("role", ""),
                    "tech_stack": p.get("tech_stack", []),
                    "achievements": p.get("achievements", ""),
                    "raw_details": p.get("raw_details", ""),
                }
                for p in raw_highlights
            ]
        if not raw_highlights and raw_projects:
            raw_highlights = [
                {
                    "name": p.get("name", ""),
                    "description": p.get("description", "") or p.get("achievements", ""),
                }
                for p in raw_projects
            ]

        raw_edu = _as_dict_list(data.get("education"))
        doc = (data.get("profile_document") or data.get("raw_summary") or "").strip()
        raw_text = data.get("raw_resume_text") or ""
        if not raw_edu and (doc or raw_text):
            raw_edu = _extract_education_from_text(f"{doc}\n{raw_text}")

        return cls(
            name=data.get("name") or "求职者",
            years_of_experience=_coerce_years(data.get("years_of_experience")),
            education=raw_edu,
            core_skills=normalized_skills,
            work_experiences=raw_work,
            projects=raw_projects,
            project_highlights=raw_highlights,
            target_positions=_as_str_list(data.get("target_positions")),
            raw_summary=doc,
            raw_resume_text=raw_text,
            profile_document=doc,
        )

    def format_for_prompt(self) -> str:
        """Format the profile into an unabbreviated, detail-rich block for LLM prompts."""
        edu_str = "; ".join(
            f"{e.get('school', '')} ({e.get('degree', '')} - {e.get('major', '')})"
            for e in self.education
        )
        skills_str = "\n".join(f"- {s}" for s in self.core_skills) if self.core_skills else "未注明"
        targets_str = ", ".join(self.target_positions)

        work_items = []
        for w in self.work_experiences:
            comp = w.get("company", "")
            role = w.get("role", "")
            start = w.get("start_date", "")
            end = w.get("end_date", "")
            time_span = f" ({start} ~ {end})" if start or end else ""
            dept = f" | 部门: {w.get('department')}" if w.get("department") else ""
            header = f"- 【{comp}】{role}{time_span}{dept}"
            details = []
            if w.get("responsibilities"):
                details.append(f"  工作职责: {w.get('responsibilities')}")
            if w.get("achievements"):
                details.append(f"  核心业绩与量化成果: {w.get('achievements')}")
            if w.get("raw_details"):
                details.append(f"  详细履历: {w.get('raw_details')}")
            work_items.append(header + ("\n" + "\n".join(details) if details else ""))
        work_str = "\n".join(work_items) if work_items else ""

        active_projects = self.projects if self.projects else self.project_highlights
        proj_items = []
        for p in active_projects:
            p_name = p.get("name", "")
            p_role = f" (角色: {p.get('role')})" if p.get("role") else ""
            start = p.get("start_date", "")
            end = p.get("end_date", "")
            p_time = f" [{start} ~ {end}]" if start or end else ""
            stack_val = p.get("tech_stack")
            stack_str = (
                f" | 技术栈: {', '.join(stack_val) if isinstance(stack_val, list) else stack_val}"
                if stack_val
                else ""
            )
            header = f"- 【{p_name}】{p_role}{p_time}{stack_str}"
            p_details = []
            if p.get("description"):
                p_details.append(f"  项目背景与架构: {p.get('description')}")
            if p.get("achievements"):
                p_details.append(f"  核心贡献与指标成果: {p.get('achievements')}")
            if p.get("raw_details"):
                p_details.append(f"  技术攻坚细节: {p.get('raw_details')}")
            proj_items.append(header + ("\n" + "\n".join(p_details) if p_details else ""))
        projects_str = "\n".join(proj_items) if proj_items else ""

        ground_truth = (
            f"\n\n[原始简历无损语料 (Ground Truth 参考)]\n{self.raw_resume_text.strip()}"
            if self.raw_resume_text and self.raw_resume_text.strip()
            else ""
        )

        parts = []
        if self.profile_document and (
            "#" in self.profile_document or len(self.profile_document) > 200
        ):
            parts.append(
                f"[候选人结构化全景画像 (Lossless Profile Document)]\n{self.profile_document.strip()}"
            )

        metadata_block = (
            f"姓名: {self.name}\n"
            f"工作经验: {self.years_of_experience}年\n"
            f"教育背景: {edu_str or '未注明'}\n"
            f"核心技能栈:\n{skills_str}\n"
            f"期望职位: {targets_str or '未注明'}"
        )
        parts.append(metadata_block)

        if work_str:
            parts.append(f"工作经历 (无损完整履历):\n{work_str}")
        if projects_str:
            parts.append(f"项目经历 (完整架构与指标):\n{projects_str}")
        # The negation of the lossless-document guard is re-tested here instead of
        # reusing a computed flag, so a short non-empty document with no '#' is dropped
        # from the prompt entirely. Pre-existing memory.py behavior, preserved verbatim.
        if (
            not ("#" in self.profile_document or len(self.profile_document) > 200)
            and self.raw_summary
        ):
            parts.append(f"个人总结与背景优势: {self.raw_summary}")

        return "\n\n".join(parts) + ground_truth


# Backwards-compatible alias. `screening.py` does `isinstance(profile,
# StructuredCandidateProfile)`, so this must be an identity alias, not a subclass.
StructuredCandidateProfile = CandidateProfile

__all__ = ["CandidateProfile", "StructuredCandidateProfile"]
