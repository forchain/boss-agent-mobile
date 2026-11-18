"""
boss_agent.screening_config
===========================
Persistence for the screening blacklist and the Screening Policy.

The policy class is *rules*; this module is *storage*. The two used to share a class:
`ScreeningPolicy` carried roughly 165 lines of YAML line-surgery, path-writability
guards and a save routine, so a domain model knew how to hand-edit a file.

Writes delegate to :func:`boss_agent.config_realm.patch_settings`, whose round-trip
serializer preserves comments and unknown sections in the human-editable file.

Tracked `*.example.*` templates are read-only inputs and are never written; a JSON
local store is not a write target either, because these appenders are YAML.
"""

from __future__ import annotations

from pathlib import Path

# Runtime, not a `TYPE_CHECKING` guard: the appenders fall back to
# `ScreeningPolicy.load_default()` when a caller supplies no policy.
from boss_agent.screening_policy import ScreeningPolicy


def load_screening_policy(config_path: str | Path | None = None) -> ScreeningPolicy:
    """Load ScreeningPolicy through the Configuration Realm chain (Issue #311, #312)."""
    from boss_agent import config_realm

    if config_path:
        target = Path(config_path)
        if target.is_file():
            data = config_realm._parse_file(target) or {}
            policy = ScreeningPolicy.from_dict(data)
            policy.source_path = str(target)
            return policy
        return ScreeningPolicy()

    chain = config_realm.resolve_chain()
    screening_keys = (
        "enable_screening",
        "title_blacklist",
        "title_whitelist",
        "jd_blacklist",
        "company_blacklist",
        "channel_preference",
        "max_commute_distance_km",
    )
    source_path = None
    for p in chain:
        if p.is_file():
            data = config_realm._parse_file(p)
            if data and any(k in data for k in screening_keys):
                source_path = str(p)
                break

    # If no file in chain had screening keys, check legacy screening files as fallback
    if source_path is None:
        try:
            from boss_agent.settings import resolve_git_common_root

            root = resolve_git_common_root()
        except Exception:
            root = Path.cwd()
        for candidate_rel in (
            Path("config/screening.local.yaml"),
            Path("config/screening.local.yml"),
            Path("config/screening.local.json"),
            Path("config/screening.yaml"),
            Path("config/screening.example.yaml"),
        ):
            cand = root / candidate_rel
            if cand.is_file():
                data = config_realm._parse_file(cand)
                if data and any(k in data for k in screening_keys):
                    policy = ScreeningPolicy.from_dict(data)
                    policy.source_path = str(cand)
                    return policy

    merged = config_realm.load_settings()
    policy = ScreeningPolicy.from_dict(merged)
    if source_path:
        policy.source_path = source_path
    return policy


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
    """Append one company to ``company_blacklist`` through the realm patch engine.

    The round-trip serializer in :mod:`boss_agent.config_realm` preserves comments and
    unknown keys; this wrapper keeps the historical name and default path resolver.
    """
    from boss_agent import config_realm

    target = Path(path) if path else resolve_writable_screening_config_path()
    return config_realm.append_company_blacklist_entry(company_name, path=target, policy=policy)


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
    """Save a ScreeningPolicy through the realm's one round-trip patch engine."""
    from boss_agent import config_realm

    return config_realm.save_screening_policy(policy, config_path=config_path)
