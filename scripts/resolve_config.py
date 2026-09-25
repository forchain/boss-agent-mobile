#!/usr/bin/env python3
"""
scripts/resolve_config.py
=========================
Print resolved Configuration Realm values as ``key=value`` lines.

The runner scripts resolved `POCKETBASE_URL`, the Appium URL and the AVD name by
mining YAML with `grep -E "^[[:space:]]*key:" | awk '{print $2}' | tr -d '"' | tr -d "'"`
— the same pipeline, copied about a dozen times across `worker.sh`, `web.sh`,
`appium.sh`, `emulator.sh`, `pocketbase.sh` and `doctor.sh`. Every copy silently
returned nothing the moment the file layout changed, and none of them knew about the
precedence chain or the environment.

This prints what the application itself would resolve:

    $ python3 scripts/resolve_config.py pocketbase_url avd_name
    pocketbase_url=https://pocketbase.example:4433
    avd_name=boss_avd_arm64

Keys that resolve to nothing print as ``key=`` so a caller can test for emptiness.
Exit status is 1 when any requested key resolved empty, which lets a script gate on a
required setting without parsing anything.
"""

import argparse
import sys
from pathlib import Path

# Add project root and src to sys.path, like the other script entrypoints.
_root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_root_dir / "src"))

from boss_agent import config_realm  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print resolved Configuration Realm values as key=value lines."
    )
    parser.add_argument(
        "keys",
        nargs="+",
        help="Realm keys to resolve, e.g. pocketbase_url avd_name server_url",
    )
    parser.add_argument(
        "--key",
        action="append",
        default=[],
        help="Print only this key's value (repeatable); for command substitution",
    )
    args = parser.parse_args(argv)

    settings = config_realm.load_settings()
    output = config_realm.format_resolved_values(args.keys, settings=settings)

    if args.key:
        values = dict(line.split("=", 1) for line in output.splitlines())
        for key in args.key:
            print(values.get(key, ""))
    else:
        print(output)

    missing = [
        key
        for key in args.keys
        if not (settings.get(key) or settings.get("chat", {}).get(key))
    ]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
