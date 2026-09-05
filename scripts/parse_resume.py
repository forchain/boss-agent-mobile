#!/usr/bin/env python3
"""
scripts/parse_resume.py
=======================
CLI entrypoint to parse resume files (.pdf, .docx, .txt, .md) and output structured candidate profiles as JSON.
"""

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "src"))

from boss_agent.memory import (  # noqa: E402
    ResumeMemoryManager,
    ResumeTextExtractor,
    StructuredCandidateProfile,
)
from droid_agent_core.llm import LLMConfig, OpenAIChatClient  # noqa: E402

log_dir = root_dir / ".boss_agent"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / "resume_parser.log"

logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("resume_parser")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse resume file into structured profile JSON")
    parser.add_argument("--file", "-f", type=str, required=True, help="Path to resume file")
    parser.add_argument(
        "--llm-config",
        type=str,
        default=None,
        help="LLM config as JSON string or path to config file",
    )
    parser.add_argument(
        "--memory-path",
        type=str,
        default="config/candidate_memory.json",
        help="Path to save candidate memory JSON",
    )
    return parser.parse_args()


def build_llm_client(llm_config_arg: str | None) -> OpenAIChatClient:
    if llm_config_arg:
        try:
            if llm_config_arg.strip().startswith("{"):
                config_data = json.loads(llm_config_arg)
                base_url = config_data.get("base_url") or "https://api.minimaxi.com/v1"
                api_key = config_data.get("api_key") or None
                model = config_data.get("model") or "MiniMax-M3"
                temp = float(config_data.get("temperature") or 0.2)

                default_cfg = LLMConfig.from_env_or_file()
                if not api_key:
                    api_key = default_cfg.api_key
                if (
                    not base_url or base_url == "https://api.openai.com/v1"
                ) and not config_data.get("api_key"):
                    base_url = default_cfg.base_url
                    model = default_cfg.model

                llm_cfg = LLMConfig(
                    provider=config_data.get("provider", default_cfg.provider),
                    base_url=base_url.rstrip("/"),
                    api_key=api_key,
                    model=model,
                    temperature=temp,
                    timeout_sec=float(config_data.get("timeout_sec") or 120.0),
                    max_tokens=int(config_data.get("max_tokens") or 262144),
                )
                return OpenAIChatClient(llm_cfg)
            else:
                llm_cfg = LLMConfig.from_env_or_file(llm_config_arg)
                return OpenAIChatClient(llm_cfg)
        except Exception as e:
            sys.stderr.write(
                f"Warning: Failed to parse custom LLM config ({e}), falling back to default.\n"
            )

    return OpenAIChatClient(LLMConfig.from_env_or_file())


def main() -> None:
    args = parse_args()
    file_path = Path(args.file)
    logger.info("Resume parsing requested for file: %s", file_path)

    if not file_path.is_file():
        msg = f"Resume file not found at: {file_path}"
        logger.error(msg)
        sys.stdout.write(json.dumps({"success": False, "message": msg}, ensure_ascii=False) + "\n")
        sys.exit(1)

    extractor = ResumeTextExtractor()
    try:
        raw_text = extractor.extract_text(file_path)
        logger.info("Extracted %d characters of text from %s", len(raw_text), file_path.name)
    except Exception as e:
        msg = f"Failed to extract text from file: {e}"
        logger.error("%s\nTraceback:\n%s", msg, traceback.format_exc())
        sys.stdout.write(json.dumps({"success": False, "message": msg}, ensure_ascii=False) + "\n")
        sys.exit(1)

    if not raw_text or not raw_text.strip():
        msg = "Extracted resume text was empty. Please check the file content."
        logger.error(msg)
        sys.stdout.write(json.dumps({"success": False, "message": msg}, ensure_ascii=False) + "\n")
        sys.exit(1)

    llm_client = build_llm_client(args.llm_config)
    logger.info(
        "Using LLM model: %s (base_url: %s, max_tokens: %d)",
        llm_client.config.model,
        llm_client.config.base_url,
        llm_client.config.max_tokens,
    )
    memory_manager = ResumeMemoryManager(
        llm_client=llm_client,
        memory_file_path=args.memory_path,
    )

    try:
        profile = memory_manager.generate_and_save_memory(file_path)
        logger.info(
            "Resume successfully parsed and saved for candidate: %s",
            profile.name,
        )
        sys.stdout.write(
            json.dumps(
                {
                    "success": True,
                    "profile": profile.to_dict(),
                    "message": "简历解析成功，已生成全量无损画像！",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    except Exception as e:
        logger.error("LLM resume parse error: %s\nTraceback:\n%s", e, traceback.format_exc())
        sys.stderr.write(f"LLM parse warning: {e}, falling back to heuristic parsing.\n")
        try:
            import re

            lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
            extracted_name = "求职者"
            for line in lines:
                m_name = re.search(r"(?:姓名|Name)[:：\s]*([^\s,，;；]+)", line)
                if m_name:
                    extracted_name = m_name.group(1).strip()
                    break
                words = line.split()
                if (
                    words
                    and len(words[0]) in [2, 3, 4]
                    and not any(
                        k in words[0] for k in ["简历", "个人", "电话", "邮箱", "求职", "求职者"]
                    )
                ):
                    extracted_name = words[0]
                    break

            exp_years = 0
            m_exp = re.search(
                r"(?<!\d)([1-9]|[1-4]\d)\s*(?:年|years|yrs)(?:研发经验|工作经验|经验)?",
                raw_text,
                re.IGNORECASE,
            )
            if m_exp:
                exp_years = int(m_exp.group(1))

            skills = []
            known_skills = [
                "Python",
                "FastAPI",
                "TypeScript",
                "Android",
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
                "LLM",
                "Agent",
                "Appium",
                "PyTorch",
                "TensorFlow",
            ]
            for s in known_skills:
                if re.search(rf"\b{re.escape(s)}\b", raw_text, re.IGNORECASE):
                    skills.append(s)

            positions = []
            known_positions = [
                "AI Agent 架构师",
                "全栈技术专家",
                "架构师",
                "技术专家",
                "算法工程师",
                "Android 开发",
                "Python 开发",
                "前端开发",
                "后端开发",
            ]
            for pos in known_positions:
                if pos in raw_text:
                    positions.append(pos)

            profile = StructuredCandidateProfile(
                name=extracted_name,
                years_of_experience=exp_years,
                education=[],
                core_skills=skills,
                project_highlights=[],
                work_experiences=[],
                projects=[],
                target_positions=positions,
                raw_summary=raw_text.strip()[:300],
                raw_resume_text=raw_text,
            )

            memory_manager.save_memory_profile(profile)

            logger.info(
                "Resume parsed via heuristic fallback and saved for candidate: %s",
                profile.name,
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "success": True,
                        "profile": profile.to_dict(),
                        "message": "简历已解析（本地规则提取模式）",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception as fallback_err:
            logger.error("Heuristic fallback failed: %s", fallback_err)
            sys.stdout.write(
                json.dumps(
                    {
                        "success": False,
                        "message": f"简历解析失败: {e}",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
