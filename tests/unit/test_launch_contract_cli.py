"""The launch-contract JSON CLI: build and rerun (spec #444, ticket #456)."""

import json

import pytest
from scripts.launch_contract import main

SEARCH = {
    "id": "search-1",
    "name": "Agent 策略",
    "keyword": "Agent",
    "target_action": "auto_apply",
    "max_jobs": 5,
    "enable_search": True,
    "enable_filter": True,
    "filter": {},
}


def _run(capsys, *args: str) -> tuple[int, dict, str]:
    code = main(list(args))
    out, err = capsys.readouterr()
    payload = json.loads(out) if out.strip() else {}
    return code, payload, err


def test_build_returns_the_contract_payload(capsys) -> None:
    code, out, _ = _run(
        capsys,
        "build",
        "--json",
        json.dumps({"kind": "search", "source": "manual", "search": SEARCH}),
    )

    assert code == 0
    assert out["ok"] is True
    assert out["task_type"] == "AUTO_APPLY"
    assert out["payload"]["target_action"] == "auto_apply"
    assert out["payload"]["min_score"] == 70
    assert out["fields"]["search_name"] == "Agent 策略"


def test_build_refuses_a_stated_mode_on_a_search(capsys) -> None:
    code, _, err = _run(
        capsys,
        "build",
        "--json",
        json.dumps({"kind": "search", "search": SEARCH, "mode": "live"}),
    )

    assert code == 2
    assert "does not take a launch mode" in json.loads(err)["error"]


def test_build_refuses_an_unknown_kind(capsys) -> None:
    code, _, err = _run(capsys, "build", "--json", json.dumps({"kind": "bogus"}))

    assert code == 2
    assert "bogus" in json.loads(err)["error"]


def test_rerun_rebuilds_a_search_from_its_inputs(capsys) -> None:
    original = {
        "id": "task-1",
        "task_type": "AUTO_APPLY",
        "payload": {
            **SEARCH,
            "saved_search_id": SEARCH["id"],
            "search_name": SEARCH["name"],
            "match_score": 99,
            "rerun_of": "stale",
        },
    }

    code, out, _ = _run(capsys, "rerun", "--json", json.dumps({"task": original}))

    assert code == 0
    assert out["payload"]["rerun_of"] == "task-1"
    assert out["payload"]["target_action"] == "auto_apply"
    assert "match_score" not in out["payload"], "contract fields are re-derived, not spread"


def test_rerun_rebuilds_a_cleanup(capsys) -> None:
    original = {
        "id": "task-2",
        "task_type": "CHECK_CHAT",
        "payload": {
            "saved_search_id": "s1",
            "search_name": "策略",
            "dry_run": True,
            "max_scan_depth": 4,
        },
    }

    code, out, _ = _run(capsys, "rerun", "--json", json.dumps({"task": original}))

    assert code == 0
    assert out["task_type"] == "CHECK_CHAT"
    assert out["payload"]["max_scan_depth"] == 4
    assert out["payload"]["rerun_of"] == "task-2"


@pytest.mark.parametrize("command", ["build", "rerun"])
def test_invalid_json_is_a_transport_error(capsys, command: str) -> None:
    code, _, err = _run(capsys, command, "--json", "{not json")

    assert code == 2
    assert "error" in json.loads(err)


def test_the_typescript_launch_mirror_is_gone() -> None:
    """ADR 0022: one owner. A deleted builder must fail loudly, not be shimmed."""
    from pathlib import Path

    root = Path(__file__).parents[2]
    assert not (root / "web" / "src" / "lib" / "taskLaunch.ts").exists()
    offenders = [
        str(path.relative_to(root))
        for path in (root / "web" / "src").rglob("*")
        if path.suffix in (".ts", ".svelte")
        and "lib/taskLaunch" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    assert not any(
        "task_launch.cases.json" in path.read_text(encoding="utf-8")
        for path in (root / "web" / "src").rglob("*")
        if path.suffix in (".ts", ".svelte")
    )
