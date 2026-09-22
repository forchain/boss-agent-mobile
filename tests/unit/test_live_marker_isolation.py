"""
tests/unit/test_live_marker_isolation.py
=======================================
Verifies the Test Runner Marker Filter Seam (spec #218, ticket #219): unadorned
`pytest` runs must never collect `live`-marked device tests, and explicit
`-m live` opt-in must still be able to collect them.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_TEST_FILE = "tests/e2e/test_live_device_smoke.py"
LIVE_TEST_NODE_ID = "test_live_app_launch_and_startup_interaction"


def _collect_only(*extra_args: str) -> str:
    """Run a collection-only pytest subprocess and return its combined output."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            *extra_args,
            LIVE_TEST_FILE,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    # Exit code 5 is pytest's "no tests collected", which is the expected outcome when
    # every test in the target file is filtered out by the marker expression.
    assert result.returncode in (0, 5), (
        f"collection failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout + result.stderr


def test_pyproject_configured_to_exclude_live_marker_by_default():
    """Verify the canonical pytest configuration carries the default live-exclusion filter."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        config = tomllib.load(f)

    addopts = config["tool"]["pytest"]["ini_options"]["addopts"]

    assert "not live" in addopts, (
        f"pytest addopts must exclude the live marker by default, got: {addopts!r}"
    )


def test_default_run_collects_zero_live_device_tests():
    """Verify an unadorned collection deselects live device tests."""
    output = _collect_only()

    assert LIVE_TEST_NODE_ID not in output, (
        f"live device test was collected by a default run:\n{output}"
    )
    assert "deselected" in output, (
        f"expected the live test to be reported as deselected, got:\n{output}"
    )


def test_explicit_live_marker_still_collects_live_device_tests():
    """Verify `-m live` overrides the default exclusion so device tests remain reachable."""
    output = _collect_only("-m", "live")

    assert LIVE_TEST_NODE_ID in output, (
        f"explicit -m live did not collect the live device test:\n{output}"
    )
    assert "deselected" not in output, (
        f"explicit -m live must not deselect the live device test:\n{output}"
    )
