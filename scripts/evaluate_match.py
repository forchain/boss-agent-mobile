#!/usr/bin/env python3
"""
scripts/evaluate_match.py
=========================
CLI entrypoint to evaluate job-candidate fit and generate customized greeting message using LLM.

Composition lives in :mod:`boss_agent.main`; this module parses the job/profile JSON and
reports the result.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "src"))

from boss_agent.core.job.use_cases.matching import offline_match_result  # noqa: E402
from boss_agent.main import (  # noqa: E402
    build_llm_client,
    build_matching_service,
)
from scripts._eval_cli import parse_candidate_profile_arg, parse_job_posting_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate job match and generate greeting")
    parser.add_argument("--job", "-j", type=str, required=True, help="Job details JSON string")
    parser.add_argument(
        "--profile", "-p", type=str, default=None, help="Candidate profile JSON string"
    )
    parser.add_argument(
        "--greeting-prompt",
        type=str,
        default=None,
        help="Greeting Prompt document text (lazy-loaded from config when omitted)",
    )
    parser.add_argument("--llm-config", type=str, default=None, help="LLM config as JSON string")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        job = parse_job_posting_json(args.job)
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"Invalid job JSON: {e}"}, ensure_ascii=False) + "\n")
        sys.exit(1)

    candidate_profile = parse_candidate_profile_arg(args.profile)

    llm_client = build_llm_client(args.llm_config)
    service = build_matching_service(
        llm_client=llm_client,
        candidate_profile=candidate_profile,
        greeting_prompt=args.greeting_prompt or None,
    )

    try:
        result = service.evaluate_and_draft_greeting(
            job, profile=candidate_profile, greeting_prompt=args.greeting_prompt
        )
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write(f"Match evaluation error: {e}\n")
        fallback = offline_match_result(job, profile=candidate_profile)
        fallback.match_reasons.insert(0, f"评估出现异常: {e}")
        sys.stdout.write(json.dumps(fallback.to_dict(), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
