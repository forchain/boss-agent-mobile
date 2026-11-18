#!/usr/bin/env python3
"""
scripts/refine_screening.py
===========================
CLI entrypoint to re-evaluate jobs with human critique or perform whole-document
Prompt Refinement of the settled Screening Prompt (ADR 0010, Spec #340).
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "src"))

from boss_agent.core.job.domain.job_entities import JobPosting  # noqa: E402
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy  # noqa: E402
from boss_agent.main import (  # noqa: E402
    build_default_screening_policy,
    build_llm_client,
    build_screener,
    resolve_screening_prompt,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-evaluate job with critique or refine the living Screening Prompt"
    )
    parser.add_argument(
        "--action",
        choices=["retest", "prompt-refine", "evaluate", "screen"],
        default="retest",
        help="Action: retest with critique, rewrite the Screening Prompt (prompt-refine), or evaluate job (evaluate/screen)",
    )
    parser.add_argument("--job", "-j", type=str, required=True, help="Job details JSON string")
    parser.add_argument("--critique", "-c", type=str, default="", help="User critique / feedback")
    parser.add_argument(
        "--original-verdict",
        type=str,
        default="",
        help="Original screening verdict before critique (for prompt-refine)",
    )
    parser.add_argument(
        "--revised-verdict",
        type=str,
        default="",
        help="Revised screening verdict after retest (for prompt-refine)",
    )
    parser.add_argument(
        "--screening-prompt",
        type=str,
        default=None,
        help="Screening Prompt document text (lazy-loaded from config when omitted)",
    )
    parser.add_argument(
        "--policy",
        type=str,
        default=None,
        help="Screening policy JSON string (loaded from settings when omitted)",
    )
    parser.add_argument("--llm-config", type=str, default=None, help="LLM config as JSON string")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        job_dict = json.loads(args.job)
        job = JobPosting(
            title=job_dict.get("job_title") or job_dict.get("title") or "目标岗位",
            company_name=job_dict.get("company_name") or job_dict.get("company") or "招聘公司",
            salary_range=job_dict.get("salary_range") or job_dict.get("salary") or "面议",
            job_description=job_dict.get("job_description") or job_dict.get("description") or "",
            recruiter_name=job_dict.get("recruiter_name") or job_dict.get("recruiter") or None,
            recruiter_title=job_dict.get("recruiter_title") or None,
        )
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"Invalid job JSON: {e}"}, ensure_ascii=False) + "\n")
        sys.exit(1)

    policy = None
    if args.policy:
        try:
            policy_dict = json.loads(args.policy)
            policy = ScreeningPolicy.from_dict(policy_dict)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse policy JSON ({e})\n")

    if policy is None:
        policy = build_default_screening_policy()

    screening_prompt = resolve_screening_prompt(args.screening_prompt)

    llm_client = build_llm_client(args.llm_config, treat_masked_key_as_missing=False)
    screener = build_screener(
        llm_client=llm_client,
        screening_prompt=screening_prompt,
    )

    if args.action == "retest":
        critique = args.critique or ""
        try:
            verdict = screener.retest_with_critique(
                job=job,
                critique=critique,
                current_prompt=args.screening_prompt,
                policy=policy,
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "success": True,
                        "approved": verdict.approved,
                        "reason": verdict.reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Retest error: {e}\n")
            # Surface the failure honestly (ADR 0010: never fabricate output).
            sys.stdout.write(
                json.dumps(
                    {
                        "success": False,
                        "error": str(e),
                        "retest_failed": True,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    elif args.action == "prompt-refine":
        orig_verdict = args.original_verdict or "淘汰 (初筛)"
        rev_verdict = args.revised_verdict or "合格保留 (重测)"
        critique = args.critique or ""
        try:
            refined_prompt = screener.refine_screening_prompt(
                job=job,
                critique=critique,
                original_verdict=orig_verdict,
                revised_verdict=rev_verdict,
                current_prompt=args.screening_prompt,
            )
            sys.stdout.write(
                json.dumps(
                    {"success": True, "refined_prompt": refined_prompt},
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Screening prompt refinement error: {e}\n")
            sys.stdout.write(
                json.dumps(
                    {
                        "success": False,
                        "error": str(e),
                        "prompt_refine_failed": True,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    elif args.action in ("evaluate", "screen"):
        try:
            verdict = screener._evaluate_jd(
                job=job,
                current_prompt=args.screening_prompt,
                policy=policy,
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "success": True,
                        "approved": verdict.approved,
                        "reason": verdict.reason,
                        "stage": verdict.stage,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Evaluate error: {e}\n")
            sys.stdout.write(
                json.dumps(
                    {
                        "success": False,
                        "error": str(e),
                        "evaluate_failed": True,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


if __name__ == "__main__":
    main()
