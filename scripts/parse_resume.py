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

from boss_agent.graph import run_resume_lifecycle_graph  # noqa: E402
from boss_agent.memory import (  # noqa: E402
    ProfileNormalizer,
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
    parser = argparse.ArgumentParser(description="Parse resume file into structured profile JSON via LangGraph")
    parser.add_argument("--file", "-f", type=str, required=True, help="Path to resume file")
    parser.add_argument("--file-name", type=str, default="", help="Original file name")
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
    parser.add_argument(
        "--user-id",
        type=str,
        default="default",
        help="User ID for profile persistence",
    )
    parser.add_argument(
        "--merge-mode",
        type=str,
        default="",
        choices=["", "initial", "merge", "overwrite"],
        help="Merge mode when existing profile exists",
    )
    parser.add_argument(
        "--await-review",
        action="store_true",
        help="Whether to stop at diff stage and await user review without persisting immediately",
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
                    timeout_sec=float(config_data.get("timeout_sec") or 300.0),
                    max_tokens=int(config_data.get("max_tokens") or 16384),
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

    try:
        lifecycle_result = run_resume_lifecycle_graph(
            file_path=str(file_path),
            file_name=args.file_name or file_path.name,
            user_id=args.user_id,
            merge_mode=args.merge_mode or None,
            await_review=args.await_review,
            llm_client=llm_client,
        )

        final_profile = (
            lifecycle_result.get("final_profile")
            or lifecycle_result.get("normalized_profile")
            or {}
        )
        diff_summary = lifecycle_result.get("diff_summary", "")

        if args.memory_path and final_profile:
            try:
                prof_obj = StructuredCandidateProfile.from_dict(final_profile)
                ResumeMemoryManager(
                    llm_client=llm_client,
                    memory_file_path=args.memory_path,
                ).save_memory_profile(prof_obj)
            except Exception as mem_err:
                logger.warning("Failed to sync profile to memory file %s: %s", args.memory_path, mem_err)

        logger.info(
            "Resume successfully processed by LangGraph lifecycle for candidate: %s",
            final_profile.get("name", "求职者"),
        )
        sys.stdout.write(
            json.dumps(
                {
                    "success": True,
                    "profile": final_profile,
                    "diff_summary": diff_summary,
                    "status": lifecycle_result.get("status", "completed"),
                    "message": "简历解析成功，已生成全量无损画像！",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    except Exception as e:
        logger.error("LangGraph resume lifecycle error: %s\nTraceback:\n%s", e, traceback.format_exc())
        sys.stderr.write(f"LangGraph parse warning: {e}, falling back to heuristic normalizer.\n")
        try:
            normalized = ProfileNormalizer.normalize({}, raw_text=raw_text)
            profile_obj = StructuredCandidateProfile.from_dict(normalized)
            if args.memory_path:
                ResumeMemoryManager(
                    llm_client=llm_client,
                    memory_file_path=args.memory_path,
                ).save_memory_profile(profile_obj)

            logger.info(
                "Resume parsed via heuristic fallback and saved for candidate: %s",
                profile_obj.name,
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "success": True,
                        "profile": profile_obj.to_dict(),
                        "diff_summary": "【解析降级】使用自愈规整器完成提取",
                        "status": "completed",
                        "message": "简历已解析（自愈规整模式）",
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
