"""
boss_agent.screening_config
===========================
Persistence for the screening blacklist and the Screening Policy.

The policy class is *rules*; this module is *storage*. The two used to share a class:
`ScreeningPolicy` carried roughly 165 lines of YAML line-surgery, path-writability
guards and a save routine, so a domain model knew how to hand-edit a file.

The surgery is line-oriented rather than a re-serialize on purpose — the screening
config is a human-editable file, and a `yaml.dump` round-trip would discard every
comment and unknown section the operator wrote.

Tracked `*.example.*` templates are read-only inputs and are never written; a JSON
local store is not a write target either, because these appenders are YAML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Runtime, not a `TYPE_CHECKING` guard: the appenders fall back to
# `ScreeningPolicy.load_default()` when a caller supplies no policy. `models` imports
# this module lazily, inside its own methods, so the cycle is closed at call time.
from .models import ScreeningPolicy

SCREENING_CONFIG_HEADER = """\
# ==============================================================================
# Boss Agent Mobile - Preliminary Job Screening Policy
# Auto-generated & updated by Boss Agent Mobile; manual edits are preserved
# ==============================================================================
"""


def is_writable_screening_path(path: str | Path) -> bool:
    """Whether `path` may be written to.

    `*.example.*` files are checked-in read-only inputs — `load_default` will
    happily resolve one on a fresh checkout, and line surgery there would edit a
    tracked file. JSON stores are excluded for the same reason the appenders are
    YAML line surgery rather than a re-serialize.
    """
    candidate = Path(path)
    return candidate.suffix in (".yaml", ".yml") and ".example." not in candidate.name


def resolve_writable_screening_config_path(root: str | Path | None = None) -> Path:
    """Resolve the writable screening configuration store.

    Always the unified local settings file, because it is the only candidate that
    actually takes effect. ``ScreeningPolicy.load_default`` resolves the *first*
    file carrying screening keys, and ``config/screening.local.yaml`` is the last
    entry on that list — behind the shipped ``config/settings.example.yaml`` — so
    writing the blacklist there would be silently shadowed and change nothing.

    Mirrors the web settings seam (``web/src/lib/server/screeningConfig.ts``).
    Checked-in ``*.example.*`` files are never returned: they are read-only inputs.
    A JSON local settings store is not a supported write target either, for the
    same reason the appenders below are line-oriented YAML surgery.
    """
    if root is None:
        try:
            from .settings import resolve_git_common_root

            base = resolve_git_common_root()
        except Exception:
            base = Path.cwd()
    else:
        base = Path(root)

    for name in ("settings.local.yaml", "settings.local.yml"):
        candidate = base / "config" / name
        if candidate.is_file():
            return candidate
    return base / "config" / "settings.local.yaml"


def append_company_blacklist_entry(
    company_name: str,
    path: str | Path | None = None,
    policy: ScreeningPolicy | None = None,
) -> bool:
    """Append one company to ``company_blacklist``, preserving the file's comments.

    Line-oriented surgery rather than a re-serialize: a full ``yaml.dump`` of the
    merged mapping would silently discard every comment and reorder the file, and
    this config is hand-maintained. Returns True when the file changed, False when
    the company was blank, already present, or the list could not be parsed safely.
    """
    cleaned = (company_name or "").strip()
    if not cleaned:
        return False

    target = Path(path) if path else resolve_writable_screening_config_path()
    if target.suffix == ".json":
        # The appenders below are line-oriented YAML surgery; a JSON store has no
        # `company_blacklist:` line to extend and would be corrupted by one.
        return False

    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot = (policy or ScreeningPolicy.load_default()).to_dict()
        if cleaned not in snapshot["company_blacklist"]:
            snapshot["company_blacklist"] = [*snapshot["company_blacklist"], cleaned]
        import yaml

        body = yaml.dump(snapshot, allow_unicode=True, sort_keys=False, default_flow_style=False)
        target.write_text(SCREENING_CONFIG_HEADER + body, encoding="utf-8")
        return True

    lines = target.read_text(encoding="utf-8").splitlines()
    key_index = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("company_blacklist:")),
        None,
    )

    if key_index is None:
        # No key to extend: add one, leaving every existing line untouched.
        suffix = [""] if lines and lines[-1].strip() else []
        entry = json.dumps(cleaned, ensure_ascii=False)
        target.write_text(
            "\n".join([*lines, *suffix, "company_blacklist:", f"  - {entry}", ""]),
            encoding="utf-8",
        )
        return True

    key_line = lines[key_index]
    indent = key_line[: len(key_line) - len(key_line.lstrip())]
    raw_value = key_line.split(":", 1)[1]
    inline_comment = ""
    if "#" in raw_value:
        raw_value, inline_comment = raw_value.split("#", 1)
        inline_comment = "  #" + inline_comment

    import yaml

    end_index = _yaml_block_end(lines, key_index)
    existing = _parse_blacklist_block(lines[key_index:end_index], indent)
    if existing is None:
        return False

    items = [str(item) for item in existing]
    if cleaned in items:
        return False

    block = [
        f"{indent}company_blacklist:{inline_comment}",
        *[f"{indent}  - {json.dumps(item, ensure_ascii=False)}" for item in [*items, cleaned]],
    ]
    rewritten = "\n".join([*lines[:key_index], *block, *lines[end_index:]])
    target.write_text(rewritten + "\n", encoding="utf-8")
    return True


def _parse_blacklist_block(block_lines: list[str], indent: str) -> list[str] | None:
    """Parse the existing `company_blacklist` value, inline or block form.

    Parses the whole `key: value` block rather than just the key line, because the
    block form carries its items on the following lines. Returns None when the
    value is not safely readable as a list — the caller then leaves the file alone
    rather than overwriting a hand-written structure it does not understand.
    """
    import yaml

    text = "\n".join(block_lines)
    if indent:
        text = "\n".join(
            line[len(indent) :] if line.startswith(indent) else line for line in text.splitlines()
        )
    try:
        parsed = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    existing = parsed.get("company_blacklist")
    return existing if isinstance(existing, list) else None


def _yaml_block_end(lines: list[str], key_index: int) -> int:
    """Exclusive end of the block value starting at `key_index`.

    Consumes indented continuation lines (the list items) and the blank lines
    between them, but stops at the first column-0 line so a following key or
    comment is never swallowed.
    """
    cursor = key_index + 1
    end = key_index + 1
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped:
            cursor += 1
            continue
        if lines[cursor][:1] in (" ", "\t"):
            end = cursor + 1
            cursor += 1
            continue
        break
    return end


def persist_company_blacklist(policy: ScreeningPolicy, company_name: str) -> Path | None:
    """Write one blacklisted company back to the policy's active config file.

    Targets the policy's ``source_path`` when it came from a file, so the addition
    lands in the config that is actually consulted. Falls back to the writable
    screening store, and never to a checked-in ``*.example.*`` file.

    Returns the written path, or None when nothing was written — the caller reports
    that rather than claiming a persistence that did not happen.

    Two unwritable sources are treated differently, because precedence decides whether
    a redirect can take effect at all. A checked-in ``*.example.*`` template sits
    *below* the unified local settings file, so redirecting there works and is what
    keeps a fresh checkout able to record a blacklist. A JSON local store sits *above*
    it, so a YAML written instead would be shadowed — that case reports failure
    instead of writing something inert.
    """
    if not policy.source_path:
        target = resolve_writable_screening_config_path()
    else:
        target = Path(policy.source_path)
        if ".example." in target.name:
            target = resolve_writable_screening_config_path()
        elif not is_writable_screening_path(target):
            return None

    written = append_company_blacklist_entry(company_name, path=target, policy=policy)
    return target if written else None


def save_policy(policy: ScreeningPolicy, config_path: str | Path | None = None) -> Path:
    """Save a ScreeningPolicy to the declarative local YAML configuration file."""
    if config_path:
        target_path = Path(config_path)
    else:
        try:
            from .settings import resolve_git_common_root

            root = resolve_git_common_root()
        except Exception:
            root = Path.cwd()
        target_path = root / "config" / "settings.local.yaml"

    target_path.parent.mkdir(parents=True, exist_ok=True)

    existing_data: dict[str, Any] = {}
    if target_path.is_file():
        try:
            import yaml

            loaded = yaml.safe_load(target_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_data = loaded
        except Exception:
            pass

    merged_data = {**existing_data, **policy.to_dict()}

    try:
        import yaml

        content = yaml.dump(
            merged_data, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
    except Exception:
        lines = [
            f"enable_screening: {'true' if policy.enable_screening else 'false'}",
            f'channel_preference: "{policy.channel_preference}"',
            "max_commute_distance_km: "
            + (
                "null"
                if policy.max_commute_distance_km is None
                else repr(float(policy.max_commute_distance_km))
            ),
            "title_whitelist:",
            *[f"  - {json.dumps(w, ensure_ascii=False)}" for w in policy.title_whitelist],
            "title_blacklist:",
            *[f"  - {json.dumps(b, ensure_ascii=False)}" for b in policy.title_blacklist],
            "company_blacklist:",
            *[f"  - {json.dumps(c, ensure_ascii=False)}" for c in policy.company_blacklist],
            "jd_blacklist:",
            *[f"  - {json.dumps(j, ensure_ascii=False)}" for j in policy.jd_blacklist],
        ]
        content = "\n".join(lines) + "\n"

    target_path.write_text(content, encoding="utf-8")
    return target_path
