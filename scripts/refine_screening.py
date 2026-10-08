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

from boss_agent.job_entities import JobPosting  # noqa: E402
from boss_agent.llm_config import create_llm_client, load_llm_config  # noqa: E402
from boss_agent.screening import CandidateScreener  # noqa: E402
from boss_agent.screening_policy import ScreeningPolicy  # noqa: E402
from droid_agent_core.llm import LLMConfig, LLMDecisionClient  # noqa: E402


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


def build_llm_client(llm_config_arg: str | None) -> LLMDecisionClient:
    if llm_config_arg:
        try:
            if llm_config_arg.strip().startswith("{"):
                config_data = json.loads(llm_config_arg)
                base_url = config_data.get("base_url") or "https://api.minimaxi.com/v1"
                api_key = config_data.get("api_key") or None
                model = config_data.get("model") or "MiniMax-M3"
                temp = float(config_data.get("temperature") or 0.2)

                default_cfg = load_llm_config()
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
                )
                return create_llm_client(config=llm_cfg)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse custom LLM config ({e}), falling back.\n")

    return create_llm_client()


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
        try:
            from boss_agent.settings import load_settings

            app_settings = load_settings()
            # load_settings() returns the merged Configuration Realm dict with the
            # blacklist lists at top level; ScreeningPolicy.from_dict reads them there.
            policy = ScreeningPolicy.from_dict(app_settings)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load default screening policy ({e})\n")
            policy = ScreeningPolicy()

    llm_client = build_llm_client(args.llm_config)
    screener = CandidateScreener(
        llm_client=llm_client,
        screening_prompt=args.screening_prompt,
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
