#!/usr/bin/env python3
"""
scripts/evaluate_match.py
=========================
CLI entrypoint to evaluate job-candidate fit and generate customized greeting message using LLM.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "src"))

from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import JobPosting
from droid_agent_core.llm import LLMConfig, OpenAIChatClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate job match and generate greeting")
    parser.add_argument("--job", "-j", type=str, required=True, help="Job details JSON string")
    parser.add_argument("--profile", "-p", type=str, default=None, help="Candidate profile JSON string")
    parser.add_argument("--llm-config", type=str, default=None, help="LLM config as JSON string")
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
                if not base_url or base_url == "https://api.openai.com/v1":
                    if not config_data.get("api_key"):
                        base_url = default_cfg.base_url
                        model = default_cfg.model

                llm_cfg = LLMConfig(
                    provider=config_data.get("provider", default_cfg.provider),
                    base_url=base_url.rstrip("/"),
                    api_key=api_key,
                    model=model,
                    temperature=temp,
                )
                return OpenAIChatClient(llm_cfg)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse custom LLM config ({e}), falling back.\n")

    return OpenAIChatClient(LLMConfig.from_env_or_file())


def main() -> None:
    args = parse_args()

    try:
        job_dict = json.loads(args.job)
        job = JobPosting(
            title=job_dict.get("job_title") or job_dict.get("title") or "目标岗位",
            company_name=job_dict.get("company_name") or job_dict.get("company") or "招聘公司",
            salary_range=job_dict.get("salary_range") or job_dict.get("salary") or "面议",
            job_description=job_dict.get("job_description") or job_dict.get("description") or "",
        )
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"Invalid job JSON: {e}"}, ensure_ascii=False) + "\n")
        sys.exit(1)

    candidate_profile = None
    if args.profile:
        try:
            profile_dict = json.loads(args.profile)
            candidate_profile = StructuredCandidateProfile.from_dict(profile_dict)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse candidate profile JSON ({e})\n")

    llm_client = build_llm_client(args.llm_config)
    service = JobMatchGreetingService(
        llm_client=llm_client,
        candidate_profile=candidate_profile,
    )

    try:
        result = service.evaluate_and_draft_greeting(job, profile=candidate_profile)
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write(f"Match evaluation error: {e}\n")
        sys.stdout.write(json.dumps({
            "match_score": 50,
            "match_reasons": [f"评估出现异常: {e}"],
            "jd_key_requirements": ["待提取"],
            "greeting_message": f"您好！看到贵司正在招聘【{job.title}】，我对该方向非常感兴趣，希望能与您沟通！"
        }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
