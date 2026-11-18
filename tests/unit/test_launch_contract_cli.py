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


@pytest.mark.parametrize("channel", ["", "all", "direct_only", "headhunter_only"])
def test_a_search_carries_its_channel_preference(capsys, channel: str) -> None:
    """The per-strategy channel is carried opaquely, including ``''`` (inherit).

    A truthiness check would drop the empty string, pinning the strategy to whatever the
    global setting happened to be that day (issue #368, ticket #370).
    """
    search = {**SEARCH, "filter": {"channel_preference": channel}}

    code, out, _ = _run(
        capsys,
        "build",
        "--json",
        json.dumps({"kind": "search", "source": "manual", "search": search}),
    )

    assert code == 0
    assert out["payload"]["filter"]["channel_preference"] == channel


@pytest.mark.parametrize("channel", ["", "all", "direct_only", "headhunter_only"])
def test_a_rerun_keeps_the_channel_preference(capsys, channel: str) -> None:
    original = {
        "id": "task-channel",
        "task_type": "AUTO_APPLY",
        "payload": {
            **SEARCH,
            "filter": {"channel_preference": channel},
            "saved_search_id": SEARCH["id"],
            "search_name": SEARCH["name"],
        },
    }

    code, out, _ = _run(capsys, "rerun", "--json", json.dumps({"task": original}))

    assert code == 0
    assert out["payload"]["filter"]["channel_preference"] == channel


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


def test_a_rerun_of_a_keyword_less_task_does_not_invent_a_keyword(capsys) -> None:
    """Pre-``keyword`` tasks searched nothing; ``""`` is that statement, not ``"agent"``."""
    original = {
        "id": "task-old",
        "task_type": "SAVE_JD",
        "payload": {"target_action": "save_jd"},
    }

    code, out, _ = _run(capsys, "rerun", "--json", json.dumps({"task": original}))

    assert code == 0
    assert out["payload"]["keyword"] == ""


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
    # A drill rerun must stay a drill. Re-deriving depth from the *current* config
    # silently turns the operator's saved 演练扫描 into live replies to recruiters.
    assert out["payload"]["dry_run"] is True


def test_rerun_keeps_a_cleanup_drill_that_was_not_a_drill(capsys) -> None:
    original = {
        "id": "task-3",
        "task_type": "CHECK_CHAT",
        "payload": {"dry_run": False},
    }

    code, out, _ = _run(capsys, "rerun", "--json", json.dumps({"task": original}))

    assert code == 0
    assert out["payload"]["dry_run"] is False


def test_a_stated_mode_outranks_a_carried_dry_run(capsys) -> None:
    code, out, _ = _run(
        capsys,
        "build",
        "--json",
        json.dumps({"kind": "chat_cleanup", "mode": "live", "dry_run": True}),
    )

    assert code == 0
    assert out["payload"]["dry_run"] is False


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
    # And the admission boundary has no second door: no Web code builds a task_type.
    assert not any(
        "createAutomationTask" in path.read_text(encoding="utf-8")
        for path in (root / "web" / "src").rglob("*")
        if path.suffix in (".ts", ".svelte")
    )
