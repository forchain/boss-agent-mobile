#!/usr/bin/env python3
"""
scripts/refine_greeting.py
==========================
CLI entrypoint to refine greetings with human critique or distill condition-action rules.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "src"))

from boss_agent.greeting_rules import GreetingStyleRule, load_greeting_rules  # noqa: E402
from boss_agent.matching import JobMatchGreetingService  # noqa: E402
from boss_agent.memory import StructuredCandidateProfile  # noqa: E402
from boss_agent.models import JobPosting  # noqa: E402
from droid_agent_core.llm import LLMConfig, OpenAIChatClient  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refine greeting with critique or distill rules")
    parser.add_argument(
        "--action",
        choices=["refine", "distill"],
        default="refine",
        help="Action to perform: refine greeting or distill rule",
    )
    parser.add_argument("--job", "-j", type=str, required=True, help="Job details JSON string")
    parser.add_argument(
        "--greeting",
        "--current-greeting",
        dest="current_greeting",
        type=str,
        default="",
        help="Current greeting message",
    )
    parser.add_argument(
        "--original-greeting",
        type=str,
        default="",
        help="Original greeting before revision (for distill)",
    )
    parser.add_argument(
        "--revised-greeting",
        type=str,
        default="",
        help="Revised greeting after revision (for distill)",
    )
    parser.add_argument("--critique", "-c", type=str, default="", help="Candidate critique/feedback")
    parser.add_argument("--history", type=str, default=None, help="Dialogue history JSON string")
    parser.add_argument("--profile", "-p", type=str, default=None, help="Candidate profile JSON string")
    parser.add_argument("--rules", "-r", type=str, default=None, help="Greeting rules JSON string")
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

    # If profile is missing or is an empty/stub placeholder, fallback to loading cached memory
    if (
        candidate_profile is None
        or (not candidate_profile.profile_document and not candidate_profile.work_experiences)
        or candidate_profile.name in ("测试候选人", "求职者", "")
    ):
        try:
            from boss_agent.memory import ResumeMemoryManager

            mgr = ResumeMemoryManager()
            cached = mgr.load_cached_memory()
            if cached and (cached.profile_document or cached.work_experiences or cached.core_skills) and (
                not candidate_profile
                or candidate_profile.name in ("测试候选人", "求职者", "")
                or not candidate_profile.profile_document
            ):
                candidate_profile = cached
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load cached candidate memory ({e})\n")

    rules = None
    if args.rules:
        try:
            rules_data = json.loads(args.rules)
            if isinstance(rules_data, list):
                rules = [GreetingStyleRule.from_dict(r) for r in rules_data if isinstance(r, dict)]
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse rules JSON ({e})\n")
    if rules is None:
        rules = load_greeting_rules()

    history = None
    if args.history:
        try:
            history = json.loads(args.history)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse history JSON ({e})\n")

    llm_client = build_llm_client(args.llm_config)
    service = JobMatchGreetingService(
        llm_client=llm_client,
        candidate_profile=candidate_profile,
    )

    if args.action == "refine":
        current_greeting = args.current_greeting or ""
        critique = args.critique or ""
        try:
            revised = service.refine_with_critique(
                job=job,
                current_greeting=current_greeting,
                critique=critique,
                history=history,
                profile=candidate_profile,
                rules=rules,
            )
            sys.stdout.write(
                json.dumps({"success": True, "revised_greeting": revised}, ensure_ascii=False)
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Refinement error: {e}\n")
            # Surface the failure honestly. Do NOT concatenate the critique
            # onto the original greeting as a fake "refined" version —
            # that violates the contract that the LLM produces the rewrite.
            sys.stdout.write(
                json.dumps(
                    {
                        "success": False,
                        "error": str(e),
                        "refinement_failed": True,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    elif args.action == "distill":
        orig_greeting = args.original_greeting or args.current_greeting or ""
        rev_greeting = args.revised_greeting or ""
        critique = args.critique or ""
        try:
            distilled_rule = service.distill_memory_rule(
                job=job,
                original_greeting=orig_greeting,
                revised_greeting=rev_greeting,
                critique=critique,
            )
            sys.stdout.write(
                json.dumps(
                    {"success": True, "rule": distilled_rule.to_dict()}, ensure_ascii=False
                )
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Distillation error: {e}\n")
            sys.stdout.write(
                json.dumps({"success": False, "error": str(e)}, ensure_ascii=False) + "\n"
            )


if __name__ == "__main__":
    main()
