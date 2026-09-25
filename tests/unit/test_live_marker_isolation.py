"""
tests/unit/test_live_marker_isolation.py
========================================
Verifies the default test-tier seam (spec #247, ticket #248): an unadorned `pytest` run
collects the fast unit suite only — never the `e2e` or `live` tiers, and never even loads
`tests/e2e/conftest.py` — while explicit invocations still reach the tiers they name.

Every scenario drives a real collection-only subprocess against the repository's own
`pyproject.toml` and root `conftest.py`, so what is asserted here is the configuration
developers and agents actually run into.
"""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
E2E_CONFTEST = REPO_ROOT / "tests" / "e2e" / "conftest.py"
LIVE_TEST_FILE = "tests/e2e/test_live_device_smoke.py"
LIVE_TEST_NODE_ID = "test_live_app_launch_and_startup_interaction"

# A throwaway pytest plugin: it records which plugins a run loaded (conftest modules are
# registered as plugins under their path) and which node ids it collected. Loaded through
# `-p _collection_probe` with its directory on PYTHONPATH.
COLLECTION_PROBE = """
import json
import os
from pathlib import Path


def pytest_sessionfinish(session, exitstatus):
    report = {
        "plugins": sorted(
            str(name) for name, _ in session.config.pluginmanager.list_name_plugin()
        ),
        "nodeids": sorted(item.nodeid for item in session.items),
    }
    Path(os.environ["PROBE_OUTPUT"]).write_text(json.dumps(report), encoding="utf-8")
"""


@pytest.fixture
def collection_probe(tmp_path: Path) -> Path:
    """Directory holding the probe plugin, ready to be put on the subprocess PYTHONPATH."""
    (tmp_path / "_collection_probe.py").write_text(COLLECTION_PROBE, encoding="utf-8")
    return tmp_path


def _collect_only(*extra_args: str, probe_dir: Path | None = None) -> subprocess.CompletedProcess:
    """Run a collection-only pytest subprocess and return the completed process.

    `--collect-only` keeps the run free of side effects: nothing is executed, so seeding a
    probe and asserting on collection can never disturb the machine under test.
    """
    argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly"]
    env = dict(os.environ)
    if probe_dir is not None:
        env["PROBE_OUTPUT"] = str(probe_dir / "probe.json")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in [str(probe_dir), env.get("PYTHONPATH", "")] if part
        )
        argv += ["-p", "_collection_probe"]

    result = subprocess.run(
        [*argv, *extra_args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    # Exit code 5 is pytest's "no tests collected", which is the expected outcome when the
    # marker expression filters out every test in the target.
    assert result.returncode in (0, 5), (
        f"collection failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
    )
    return result


def _output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def _probe_report(result: subprocess.CompletedProcess, probe_dir: Path) -> dict[str, list[str]]:
    """Read the probe report, surfacing pytest output when the probe never ran."""
    report_path = probe_dir / "probe.json"
    assert report_path.exists(), f"the probe plugin never ran:\n{_output(result)}"
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_pyproject_restricts_the_default_run_to_the_fast_unit_tier():
    """Verify the canonical pytest configuration carries both default-tier restrictions."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        config = tomllib.load(f)

    ini_options = config["tool"]["pytest"]["ini_options"]
    addopts = ini_options["addopts"]

    assert ini_options["testpaths"] == ["tests/unit"], (
        f"the default run must collect the unit tier only, got: {ini_options['testpaths']!r}"
    )
    assert "not live" in addopts, (
        f"pytest addopts must exclude the live marker by default, got: {addopts!r}"
    )
    assert "not e2e" in addopts, (
        f"pytest addopts must exclude the e2e marker by default, got: {addopts!r}"
    )
    assert any(marker.startswith("e2e") for marker in ini_options["markers"]), (
        f"the e2e marker must be declared, got: {ini_options['markers']!r}"
    )


def test_default_run_collects_only_fast_unit_tests(collection_probe: Path):
    """Verify an unadorned run stays inside `tests/unit` and never loads the E2E conftest."""
    result = _collect_only(probe_dir=collection_probe)
    report = _probe_report(result, collection_probe)

    assert report["nodeids"], "an unadorned collection must still collect the unit suite"
    strays = [nodeid for nodeid in report["nodeids"] if not nodeid.startswith("tests/unit/")]
    assert not strays, f"the default run collected tests outside the unit tier:\n{strays}"
    assert str(E2E_CONFTEST) not in report["plugins"], (
        "the default run loaded tests/e2e/conftest.py:\n" + "\n".join(report["plugins"])
    )


def test_default_run_collects_zero_live_device_tests():
    """Verify device tests stay deselected even when the run targets a device test file."""
    output = _output(_collect_only(LIVE_TEST_FILE))

    assert LIVE_TEST_NODE_ID not in output, (
        f"live device test was collected by a default run:\n{output}"
    )
    assert "deselected" in output, (
        f"expected the live test to be reported as deselected, got:\n{output}"
    )


def test_marker_only_invocation_reaches_the_live_tier():
    """Verify `pytest -m live` still selects device tests without naming a path.

    The default `testpaths` covers `tests/unit` alone, so a marker expression on its own
    has nothing to select from; the root conftest widens the collection root for exactly
    that invocation shape.
    """
    output = _output(_collect_only("-m", "live"))

    assert LIVE_TEST_NODE_ID in output, (
        f"bare `-m live` did not reach the live device test:\n{output}"
    )


def test_explicit_e2e_path_runs_the_e2e_suite(collection_probe: Path):
    """Verify pointing at `tests/e2e` lifts the default e2e deselection — device tests aside."""
    result = _collect_only("tests/e2e", probe_dir=collection_probe)
    report = _probe_report(result, collection_probe)

    assert report["nodeids"], (
        "pointing at tests/e2e must run the E2E suite, not deselect all of it:\n" + _output(result)
    )
    strays = [nodeid for nodeid in report["nodeids"] if not nodeid.startswith("tests/e2e/")]
    assert not strays, f"an explicit E2E run collected tests outside tests/e2e:\n{strays}"
    assert LIVE_TEST_NODE_ID not in _output(result), (
        "the live guard must survive an explicit E2E path:\n" + _output(result)
    )
    # Positive control for the probe: the E2E conftest *is* loaded when its suite runs.
    assert str(E2E_CONFTEST) in report["plugins"], (
        "the E2E conftest must load when tests/e2e is collected:\n" + "\n".join(report["plugins"])
    )


def test_marker_only_invocation_reaches_the_e2e_tier(collection_probe: Path):
    """Verify `pytest -m e2e` selects the service tests without naming a path."""
    result = _collect_only("-m", "e2e", probe_dir=collection_probe)
    report = _probe_report(result, collection_probe)

    assert report["nodeids"], (
        "bare `-m e2e` must run the E2E suite, not collect nothing:\n" + _output(result)
    )
    strays = [nodeid for nodeid in report["nodeids"] if not nodeid.startswith("tests/e2e/")]
    assert not strays, f"`-m e2e` collected tests outside the E2E tier:\n{strays}"
    assert LIVE_TEST_NODE_ID not in _output(result), (
        "the live guard must survive `-m e2e`:\n" + _output(result)
    )


@pytest.mark.parametrize(
    "expression",
    ["not live", "not  live", "not\tlive", "not (live)", "not e2e"],
    ids=["single-space", "double-space", "tab", "parenthesised", "other-tier"],
)
def test_filter_only_marker_expression_keeps_the_default_scope(
    collection_probe: Path, expression: str
):
    """Verify a negated tier mention filters the unit tier instead of widening into E2E.

    A negated mention is a filter over whatever is in scope, not a request for another tier:
    only `-m live` / `-m e2e` pull the wider collection root in. How the negation is spelled
    must not matter, because pytest's own tokenizer accepts each of these forms and any one
    of them that widened instead would collect — and run — the real services in `tests/e2e`.
    """
    result = _collect_only("-m", expression, probe_dir=collection_probe)
    report = _probe_report(result, collection_probe)

    assert report["nodeids"], f"`-m {expression!r}` must still collect the unit suite"
    strays = [nodeid for nodeid in report["nodeids"] if not nodeid.startswith("tests/unit/")]
    assert not strays, f"the filter {expression!r} widened the run:\n{strays}"


def test_marker_flag_inside_a_short_cluster_still_reaches_the_tier(collection_probe: Path):
    """Verify `pytest -qm e2e` is read as the tier request pytest's own parser sees.

    pytest accepts `-m` bundled into a short-flag cluster, so detection scans the whole short
    token. Reading a bundled flag as absent would leave the expression nothing to select from
    and exit 5 on a run the developer meant to widen.
    """
    result = _collect_only("-qm", "e2e", probe_dir=collection_probe)
    report = _probe_report(result, collection_probe)

    assert report["nodeids"], "`-qm e2e` collected nothing:\n" + _output(result)
    strays = [nodeid for nodeid in report["nodeids"] if not nodeid.startswith("tests/e2e/")]
    assert not strays, f"`-qm e2e` collected tests outside the E2E tier:\n{strays}"


def test_broad_path_still_deselects_both_remote_tiers(collection_probe: Path):
    """Verify `pytest tests` — a broad path, no marker — deselects E2E and device tests."""
    result = _collect_only("tests", probe_dir=collection_probe)
    report = _probe_report(result, collection_probe)

    assert report["nodeids"], "a broad path must still collect the unit suite"
    strays = [
        nodeid
        for nodeid in report["nodeids"]
        if nodeid.startswith("tests/e2e/") or LIVE_TEST_NODE_ID in nodeid
    ]
    assert not strays, f"a broad path collected tier-excluded tests:\n{strays}"
