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

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile  # noqa: E402
from boss_agent.core.job.domain.job_entities import JobPosting  # noqa: E402
from boss_agent.core.shared.identifier_helpers import format_recruiter_greeting_prefix  # noqa: E402
from boss_agent.main import (  # noqa: E402
    build_llm_client,
    build_matching_service,
    build_resume_memory_manager,
)


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
        job_dict = json.loads(args.job)
        job = JobPosting(
            title=job_dict.get("job_title") or job_dict.get("title") or "目标岗位",
            company_name=job_dict.get("company_name") or job_dict.get("company") or "招聘公司",
            salary_range=job_dict.get("salary_range") or job_dict.get("salary") or "面议",
            job_description=job_dict.get("job_description") or job_dict.get("description") or "",
            recruiter_name=job_dict.get("recruiter_name") or job_dict.get("recruiter") or None,
            recruiter_title=job_dict.get("recruiter_title") or None,
            tags=job_dict.get("tags") or [],
            digest=job_dict.get("digest") or "",
            search_filter=job_dict.get("search_filter") or None,
        )
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"Invalid job JSON: {e}"}, ensure_ascii=False) + "\n")
        sys.exit(1)

    candidate_profile = None
    if args.profile:
        try:
            profile_dict = json.loads(args.profile)
            from boss_agent.core.candidate.domain.profile_normalizer import ProfileNormalizer

            norm_dict = ProfileNormalizer.normalize(profile_dict)
            candidate_profile = CandidateProfile.from_dict(norm_dict)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse candidate profile JSON ({e})\n")

    # If profile is missing or is an empty/stub placeholder, fallback to loading cached memory
    if (
        candidate_profile is None
        or (not candidate_profile.profile_document and not candidate_profile.work_experiences)
        or candidate_profile.name in ("测试候选人", "求职者", "")
    ):
        try:
            cached = build_resume_memory_manager().load_cached_memory()
            if (
                cached
                and (cached.profile_document or cached.work_experiences or cached.core_skills)
                and (
                    not candidate_profile
                    or candidate_profile.name in ("测试候选人", "求职者", "")
                    or not candidate_profile.profile_document
                )
            ):
                candidate_profile = cached
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load cached candidate memory ({e})\n")

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
        prefix = format_recruiter_greeting_prefix(getattr(job, "recruiter_name", None))
        sys.stdout.write(
            json.dumps(
                {
                    "match_score": 50,
                    "match_reasons": [f"评估出现异常: {e}"],
                    "jd_key_requirements": ["待提取"],
                    "greeting_message": f"{prefix}看到贵司正在招聘【{job.title}】，我对该方向非常感兴趣，希望能与您沟通！",
                },
                ensure_ascii=False,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
