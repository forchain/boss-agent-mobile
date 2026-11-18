"""
boss_agent.core.candidate.use_cases.resume_lifecycle
====================================================
Resume lifecycle steps (extract, structure, normalize, diff, persist) as framework-free
callables with injected dependencies (ADR 0008; extracted from the LangGraph nodes by
Spec #443).

The LangGraph ``StateGraph`` wiring stays in ``boss_agent.graph``; these functions move
the node logic so ingestion/normalization/diffing rules do not depend on the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.candidate.domain.profile_normalizer import ProfileNormalizer
from boss_agent.core.candidate.interfaces.candidate_memory_store import CandidateMemoryStore
from boss_agent.core.candidate.interfaces.resume_text_source import ResumeTextSource

_RESUME_PROFILE_PROMPT = (
    "请全面、深度、无损地解析以下求职者原始简历文本，并以标准严格的 JSON 格式输出：\n\n"
    "[简历文本内容]\n{raw_text}\n\n"
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
    '  "work_experiences": [],\n'
    '  "projects": []\n'
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

_RESUME_PROFILE_MESSAGES = (
    {
        "role": "system",
        "content": "You are a professional HR assistant specializing in parsing candidate resumes into structured JSON with 100% fidelity. Always output valid, complete JSON.",
    },
)


def extract_resume_text(
    text_source: ResumeTextSource,
    *,
    file_path: str = "",
    raw_resume_text: str = "",
    file_name: str = "",
) -> dict[str, Any]:
    """Extract raw ground-truth text from the resume file when the state has none."""
    raw_text = raw_resume_text
    if not raw_text and file_path:
        raw_text = text_source.extract_text(file_path)

    resolved_name = file_name or (Path(file_path).name if file_path else "resume.txt")
    return {
        "raw_resume_text": raw_text,
        "file_name": resolved_name,
        "status": "text_extracted",
    }


def generate_profile_document(llm_client: Any, raw_resume_text: str) -> dict[str, Any]:
    """Generate the structured profile document, degrading to the raw text on LLM failure."""
    if not raw_resume_text.strip():
        return {
            "status": "error",
            "error_message": "Empty resume text content.",
        }

    prompt = _RESUME_PROFILE_PROMPT.replace("{raw_text}", raw_resume_text)
    messages = [*_RESUME_PROFILE_MESSAGES, {"role": "user", "content": prompt}]

    extra_payload = None
    cfg = getattr(llm_client, "config", None)
    if cfg and (
        "minimax" in getattr(cfg, "base_url", "").lower()
        or "minimax" in getattr(cfg, "model", "").lower()
    ):
        extra_payload = {"thinking": {"type": "disabled"}}

    try:
        res = llm_client.chat_completion_json(
            messages, max_tokens=16384, extra_payload=extra_payload
        )
    except Exception:
        res = {"raw_summary": raw_resume_text[:500]}

    return {
        "extracted_metadata": res,
        "profile_document": res.get("profile_document") or res.get("raw_summary") or "",
        "status": "document_generated",
    }


def normalize_extracted_profile(
    extracted_metadata: dict[str, Any] | None,
    raw_resume_text: str = "",
    profile_document: str = "",
) -> dict[str, Any]:
    """Self-heal the extracted fields into the canonical profile shape."""
    extracted = dict(extracted_metadata or {})
    if not extracted.get("profile_document") and profile_document:
        extracted["profile_document"] = profile_document

    normalized = ProfileNormalizer.normalize(extracted, raw_text=raw_resume_text)
    profile_obj = CandidateProfile.from_dict(normalized)
    return {
        "normalized_profile": profile_obj.to_dict(),
        "profile_document": profile_obj.profile_document,
        "status": "normalized",
    }


def analyze_profile_diff(
    existing_profile: dict[str, Any] | None,
    normalized_profile: dict[str, Any] | None,
    merge_mode: str = "",
) -> dict[str, Any]:
    """Compare the incoming profile with the stored one and decide how to merge it."""
    new_prof = normalized_profile or {}
    if not existing_profile or (
        not existing_profile.get("name") and not existing_profile.get("raw_summary")
    ):
        return {
            "existing_profile": None,
            "diff_summary": "【初次录入】未发现历史画像记录，已自动完成全量初始化解析。",
            "merge_mode": merge_mode or "initial",
            "status": "diff_ready",
        }

    # Compare old vs new
    diffs: list[str] = []
    old_name = existing_profile.get("name", "")
    new_name = new_prof.get("name", "")
    if new_name and new_name != old_name:
        diffs.append(f"- 姓名变更: {old_name or '(未设)'} -> {new_name}")

    old_exp = existing_profile.get("years_of_experience", 0)
    new_exp = new_prof.get("years_of_experience", 0)
    if new_exp and new_exp != old_exp:
        diffs.append(f"- 经验年限: {old_exp}年 -> {new_exp}年")

    old_pos = set(existing_profile.get("target_positions") or [])
    new_pos = set(new_prof.get("target_positions") or [])
    added_pos = new_pos - old_pos
    if added_pos:
        diffs.append(f"- 新增期望职位: {', '.join(added_pos)}")

    old_skills = set(existing_profile.get("core_skills") or [])
    new_skills = set(new_prof.get("core_skills") or [])
    added_skills = new_skills - old_skills
    if added_skills:
        sample_added = list(added_skills)[:5]
        diffs.append(f"- 新增技能标签: {len(added_skills)} 项 ({', '.join(sample_added)})")

    diffs.append("- 全景画像文档: 最新解析版本已就绪，覆盖架构与项目细节")

    return {
        "existing_profile": existing_profile,
        "diff_summary": "\n".join(diffs),
        "merge_mode": merge_mode or "merge",
        "status": "diff_ready",
    }


async def persist_resume_profile(
    store: CandidateMemoryStore,
    *,
    user_id: str,
    normalized_profile: dict[str, Any] | None,
    existing_profile: dict[str, Any] | None,
    merge_mode: str,
    file_name: str,
    raw_resume_text: str,
    diff_summary: str,
) -> dict[str, Any]:
    """Merge the incoming profile into the stored one and record a resume revision."""
    incoming = normalized_profile or {}

    if merge_mode in ["overwrite", "initial"] or not existing_profile:
        final = dict(incoming)
        final["work_experiences"] = final.get("work_experiences") or []
        final["projects"] = final.get("projects") or []
        final["project_highlights"] = final.get("project_highlights") or []
        final["education"] = final.get("education") or []
        final["core_skills"] = final.get("core_skills") or []
        final["target_positions"] = final.get("target_positions") or []
        incoming_doc = incoming.get("profile_document") or incoming.get("raw_summary") or ""
        final["profile_document"] = incoming_doc
        final["raw_summary"] = incoming_doc
    else:
        # Merge mode: union of skills and positions, keep latest incoming document
        final = dict(existing_profile)
        if incoming.get("name"):
            final["name"] = incoming["name"]
        if incoming.get("years_of_experience"):
            final["years_of_experience"] = incoming["years_of_experience"]

        final["core_skills"] = list(
            dict.fromkeys(
                (existing_profile.get("core_skills") or []) + (incoming.get("core_skills") or [])
            )
        )
        final["target_positions"] = list(
            dict.fromkeys(
                (existing_profile.get("target_positions") or [])
                + (incoming.get("target_positions") or [])
            )
        )

        incoming_doc = incoming.get("profile_document") or incoming.get("raw_summary") or ""
        final["profile_document"] = incoming_doc
        final["raw_summary"] = incoming_doc
        final["raw_resume_text"] = incoming.get("raw_resume_text") or existing_profile.get(
            "raw_resume_text", ""
        )
        final["work_experiences"] = (
            incoming.get("work_experiences") or existing_profile.get("work_experiences") or []
        )
        final["projects"] = incoming.get("projects") or existing_profile.get("projects") or []
        final["project_highlights"] = (
            incoming.get("project_highlights") or existing_profile.get("project_highlights") or []
        )
        final["education"] = incoming.get("education") or existing_profile.get("education") or []

    saved_prof = await store.save_candidate_profile(final, user_id=user_id)
    resolved_name = file_name or "resume.txt"
    file_type = Path(resolved_name).suffix.lstrip(".") or "txt"
    rev_data = {
        "user_id": user_id,
        "file_name": resolved_name,
        "file_type": file_type,
        "file_size": len(raw_resume_text.encode("utf-8")),
        "extracted_text": raw_resume_text,
        "diff_summary": diff_summary,
    }
    rev_rec = await store.create_resume_revision(rev_data, user_id=user_id)
    return {
        "final_profile": saved_prof,
        "revision_record": rev_rec,
        "status": "completed",
    }
