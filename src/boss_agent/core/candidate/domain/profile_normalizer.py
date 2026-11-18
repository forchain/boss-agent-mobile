"""
boss_agent.core.candidate.domain.profile_normalizer
===================================================
Self-healing rules that turn raw LLM profile output into the canonical profile shape
(moved out of ``memory.py`` by Spec #443; stdlib only).
"""

from __future__ import annotations

from typing import Any

from .candidate_profile import extract_education_from_text


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
        -- it never used ``cls`` -- so it moved to ``candidate_profile`` where
        ``CandidateProfile.from_dict`` can call it without importing this module.
        This method stays so existing callers keep working.
        """
        return extract_education_from_text(text)
