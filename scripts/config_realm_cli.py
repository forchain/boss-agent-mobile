#!/usr/bin/env python3
"""JSON transport for the Configuration Realm (spec #444, ticket #445).

The realm owns every default, precedence rule, coercion and error; this script is a
transport adapter only. The Web Dashboard reads and patches through it instead of
re-deriving the merge in TypeScript.

    python3 scripts/config_realm_cli.py read
    python3 scripts/config_realm_cli.py patch --json '{"daily_greeting_limit": 15}'

Both commands print one JSON object on stdout. Errors print JSON on stderr and exit 2.
"""

import argparse
import json
import sys
from pathlib import Path

_root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_root_dir / "src"))

from boss_agent import config_realm  # noqa: E402


def _emit_error(message: str) -> int:
    print(json.dumps({"error": message}), file=sys.stderr)
    return 2


def _read_patch(args: argparse.Namespace) -> dict:
    if args.json is not None:
        return json.loads(args.json)
    if args.stdin:
        return json.loads(sys.stdin.read())
    raise ValueError("patch requires --json <object> or --stdin")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configuration Realm JSON transport.")
    parser.add_argument("--config-path", default=None, help="Explicit settings file.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("read", help="Print the merged settings and screening policy.")

    patch = sub.add_parser("patch", help="Apply a partial settings object.")
    patch.add_argument("--json", default=None, help="JSON object to merge.")
    patch.add_argument("--stdin", action="store_true", help="Read the JSON object from stdin.")

    args = parser.parse_args(argv)

    try:
        if args.command == "read":
            print(json.dumps(config_realm.read_config(args.config_path)))
            return 0

        payload = _read_patch(args)
        target = config_realm.patch_settings(payload, config_path=args.config_path)
        print(
            json.dumps(
                {"ok": True, "path": str(target), **config_realm.read_config(args.config_path)}
            )
        )
        return 0
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _emit_error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
