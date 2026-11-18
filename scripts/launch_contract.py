#!/usr/bin/env python3
"""JSON transport for the launch contract (spec #444, ticket #456).

The Web Dashboard sends a launch *intent*; the contract owner in
:mod:`boss_agent.task_launch` builds and validates the payload. This script is a
transport adapter, not a second business layer.

    python3 scripts/launch_contract.py build --json '{"kind":"search","source":"manual","search":{...}}'
    python3 scripts/launch_contract.py rerun --json '{"task":{...},"source":"manual"}'

Prints one JSON object on stdout; a refused launch prints ``{"error": ...}`` on stderr
and exits 2.
"""

import argparse
import json
import sys
from pathlib import Path

_root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_root_dir / "src"))

from boss_agent.search_entities import SavedSearch  # noqa: E402
from boss_agent.task_launch import (  # noqa: E402
    LaunchContractError,
    LaunchMode,
    LaunchSource,
    TaskKind,
    build_launch,
    rebuild_rerun_launch,
)


def _launch_json(launch) -> dict:
    payload = launch.payload
    return {
        "task_type": launch.task_type.value,
        "payload": payload,
        "source": launch.source.value,
        "fields": {
            "target_action": payload.get("target_action"),
            "keyword": payload.get("keyword"),
            "search_name": payload.get("search_name"),
            "max_jobs": payload.get("max_jobs"),
            "min_score": payload.get("min_score"),
        },
    }


def _build(request: dict) -> dict:
    kind = TaskKind(request["kind"])
    source = LaunchSource(request.get("source") or LaunchSource.MANUAL.value)
    raw_search = request.get("search")
    search = SavedSearch.from_dict(raw_search) if raw_search else None
    mode = request.get("mode")
    kwargs = {
        key: request.get(key)
        for key in (
            "candidate_profile",
            "min_score",
            "rejection_reply_text",
            "max_scan_depth",
            "max_scroll_swipes",
        )
        if request.get(key) is not None
    }
    launch = build_launch(
        kind,
        source=source,
        search=search,
        mode=LaunchMode(mode) if mode else None,
        **kwargs,
    )
    return _launch_json(launch)


def _rerun(request: dict) -> dict:
    source = LaunchSource(request.get("source") or LaunchSource.MANUAL.value)
    launch = rebuild_rerun_launch(request["task"], source=source)
    return _launch_json(launch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch contract JSON transport.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "rerun"):
        command = sub.add_parser(name)
        command.add_argument("--json", required=True)
        command.add_argument("--stdin", action="store_true")

    args = parser.parse_args(argv)
    try:
        request = json.loads(sys.stdin.read() if args.stdin else args.json)
        result = _build(request) if args.command == "build" else _rerun(request)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (LaunchContractError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
