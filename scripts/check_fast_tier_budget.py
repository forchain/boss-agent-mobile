#!/usr/bin/env python
"""Fail when the Fast Unit tier exceeds its wall-clock budget.

Spec #303's thesis is that the project's claims about itself must be verifiable. The Fast
Unit tier claims it "finishes in under 60 seconds" (docs/agents/testing.md, GLOSSARY.md).
This script is what turns that sentence into a fact: it runs the tier the way CI does and
exits non-zero when the measured wall-clock crosses ``BUDGET_SECONDS``, so the number cannot
quietly drift back into fiction.

Run it exactly as CI does:

    uv run --extra dev python scripts/check_fast_tier_budget.py
"""

from __future__ import annotations

import re
import subprocess
import sys

# The budget the docs promise. Amending it means amending docs/agents/testing.md and the
# GLOSSARY.md glossary in the same change — the point is that the number and the prose agree.
BUDGET_SECONDS = 60.0

# pytest's summary line, with or without trailing warnings: "1119 passed in 76.97s".
SUMMARY_RE = re.compile(r"(\d+) passed\b[^\n]*?\bin ([\d.]+)s")


def main() -> int:
    # No `-p no:randomly`: the budget must hold for the order CI actually runs.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "-q"],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    if proc.returncode != 0:
        print("Fast Unit tier failed; budget not evaluated.", file=sys.stderr)
        return proc.returncode

    matches = list(SUMMARY_RE.finditer(proc.stdout))
    if not matches:
        print("Could not read the tier's elapsed time from pytest output.", file=sys.stderr)
        return 2

    # The last summary line wins, in case pytest ever repeats it.
    passed, seconds = int(matches[-1].group(1)), float(matches[-1].group(2))
    print(f"\nFast Unit tier: {passed} passed in {seconds:.2f}s (budget {BUDGET_SECONDS:.0f}s)")
    if seconds > BUDGET_SECONDS:
        print(
            f"::error::Fast Unit tier took {seconds:.2f}s, over its {BUDGET_SECONDS:.0f}s budget. "
            "Speed it up, or amend the budget in docs/agents/testing.md and GLOSSARY.md together.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
