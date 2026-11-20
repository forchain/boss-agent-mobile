#!/usr/bin/env python3
"""
scripts/refine_greeting.py
==========================
CLI entrypoint to refine greetings with human critique or perform whole-document
Prompt Refinement of the settled Greeting Prompt (ADR 0010).

Composition lives in :mod:`boss_agent.main`; this module parses the JSON arguments and
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

from boss_agent.main import (  # noqa: E402
    build_llm_client,
    build_matching_service,
)
from scripts._eval_cli import parse_candidate_profile_arg, parse_job_posting_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refine greeting with critique or refine the Greeting Prompt"
    )
    parser.add_argument(
        "--action",
        choices=["refine", "prompt-refine"],
        default="refine",
        help="Action: refine greeting with critique, or rewrite the Greeting Prompt (Prompt Refinement)",
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
        help="Original greeting before revision (for prompt-refine)",
    )
    parser.add_argument(
        "--revised-greeting",
        type=str,
        default="",
        help="Revised greeting after revision (for prompt-refine)",
    )
    parser.add_argument(
        "--critique", "-c", type=str, default="", help="Candidate critique/feedback"
    )
    parser.add_argument("--history", type=str, default=None, help="Dialogue history JSON string")
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

    history = None
    if args.history:
        try:
            history = json.loads(args.history)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse history JSON ({e})\n")

    llm_client = build_llm_client(args.llm_config, treat_masked_key_as_missing=False)
    service = build_matching_service(
        llm_client=llm_client,
        candidate_profile=candidate_profile,
        greeting_prompt=args.greeting_prompt or None,
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
                greeting_prompt=args.greeting_prompt,
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
    elif args.action == "prompt-refine":
        orig_greeting = args.original_greeting or args.current_greeting or ""
        rev_greeting = args.revised_greeting or ""
        critique = args.critique or ""
        try:
            refined_prompt = service.refine_greeting_prompt(
                job=job,
                original_greeting=orig_greeting,
                revised_greeting=rev_greeting,
                critique=critique,
                current_prompt=args.greeting_prompt,
            )
            sys.stdout.write(
                json.dumps({"success": True, "refined_prompt": refined_prompt}, ensure_ascii=False)
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Prompt refinement error: {e}\n")
            # Never fabricate a prompt rewrite — the document is the candidate's
            # settled memory and only the LLM (or the candidate) may change it.
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


if __name__ == "__main__":
    main()
