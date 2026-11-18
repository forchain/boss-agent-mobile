"""
boss_agent.adapters.outbound.config.settings
=============================================
Centralized configuration loading and PocketBase URL/database path resolution.
"""

import logging
import os
from pathlib import Path
from typing import Any

from boss_agent.core.chat.domain.rejection import (
    DEFAULT_MAX_SCAN_DEPTH,
    DEFAULT_MAX_SCROLL_SWIPES,
    DEFAULT_REJECTION_REPLY_TEXT,
    ChatAcknowledgmentSettings,
    coerce_bool,
    coerce_positive_int,
)

from . import config_realm

try:
    # Rebound to None by tests that exercise the no-PyYAML fallback parser. The realm
    # reads this module attribute late, so the rebinding still has its effect.
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger("boss_agent.settings")

#: The Configuration Realm's chain, highest precedence first. Declared here because this
#: name is the historical test/embedder seam; the realm reads it late so a rebinding is
#: honoured. The values themselves live in
#: :mod:`boss_agent.adapters.outbound.config.config_realm`.
DEFAULT_CONFIG_SEARCH_PATHS: list[Path] = list(config_realm.CONFIG_CHAIN)

DEFAULT_POCKETBASE_URL: str = config_realm.DEFAULT_POCKETBASE_URL
DEFAULT_POCKETBASE_DATA_DIR: str = config_realm.DEFAULT_POCKETBASE_DATA_DIR
DEFAULT_POCKETBASE_DB_PATH: str = config_realm.DEFAULT_POCKETBASE_DB_PATH
DEFAULT_SERVER_URL: str = config_realm.DEFAULT_SERVER_URL

#: The one masked-secret predicate, re-exported for the scripts that used to carry
#: their own copy of it.
is_mask_placeholder = config_realm.is_mask_placeholder


def normalize_url(url: str) -> str:
    """Normalize URL by stripping surrounding whitespace and trailing slashes."""
    return config_realm.normalize_url(url)


def invalidate_cache() -> None:
    """Drop the cached Configuration Realm load (the explicit invalidation seam)."""
    config_realm.invalidate_cache()


def resolve_chat_acknowledgment_settings(
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> ChatAcknowledgmentSettings:
    """Resolve the rejection triage settings.

    Precedence:
      1. `CHAT_REJECTION_REPLY_TEXT` / `CHAT_MAX_SCAN_DEPTH` / `CHAT_DRY_RUN`
         environment variables
      2. The nested `chat:` block of the merged settings files
      3. Built-in defaults ("收到 谢谢", 30, false)

    Pass `settings` to resolve from an already-loaded mapping (used by callers
    that have one, and by tests that must stay independent of local config).
    """
    merged = settings if settings is not None else load_settings(config_path=config_path)
    chat_block = merged.get("chat")
    chat = config_realm.apply_chat_env_overrides(chat_block if isinstance(chat_block, dict) else {})

    reply_text = chat.get("rejection_reply_text")
    scan_depth = chat.get("max_scan_depth")
    scroll_swipes = chat.get("max_scroll_swipes")
    dry_run = chat.get("dry_run")

    reply_str = str(reply_text).strip() if reply_text is not None else ""
    return ChatAcknowledgmentSettings(
        rejection_reply_text=reply_str or DEFAULT_REJECTION_REPLY_TEXT,
        max_scan_depth=coerce_positive_int(scan_depth, DEFAULT_MAX_SCAN_DEPTH),
        max_scroll_swipes=coerce_positive_int(scroll_swipes, DEFAULT_MAX_SCROLL_SWIPES),
        dry_run=coerce_bool(dry_run, default=False),
    )


def resolve_run_cleanup_on_startup(
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> bool:
    """Resolve whether a starting service queues a 拒信清扫 task before searching.

    Precedence:
      1. Environment variable declared in ``config_realm.ENV_OVERRIDES["run_cleanup_on_startup"]``
      2. The `run_cleanup_on_startup` settings key
      3. Built-in default: True. Searching with a stale company blacklist is the
         failure this exists to prevent, so the safe default is to clean first.
    """
    merged = settings if settings is not None else load_settings(config_path=config_path)
    env_value = config_realm.resolve_env_override("run_cleanup_on_startup")
    raw: Any = env_value if env_value is not None else merged.get("run_cleanup_on_startup")

    return coerce_bool(raw, default=True)


def resolve_enable_scheduler(
    explicit: bool | None = None,
    config_path: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> bool:
    """Resolve whether the integrated Cron scheduler daemon is enabled.

    Precedence:
      1. Explicit programmatic/CLI argument (`explicit`, if not None)
      2. Environment variable declared in ``config_realm.ENV_OVERRIDES["enable_scheduler"]``
      3. The `enable_scheduler` settings key
      4. Built-in default: True.
    """
    if explicit is not None:
        return bool(explicit)

    merged = settings if settings is not None else load_settings(config_path=config_path)
    env_value = config_realm.resolve_env_override("enable_scheduler")
    raw: Any = env_value if env_value is not None else merged.get("enable_scheduler")

    return coerce_bool(raw, default=True)


def resolve_pocketbase_url(
    explicit_url: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    """Resolve PocketBase State Stream Broker URL according to precedence hierarchy:

    1. Explicit programmatic/CLI argument (`explicit_url`)
    2. Environment overrides and configuration files via Configuration Realm
    3. Fallback default (`http://127.0.0.1:8090`)
    """
    if explicit_url and explicit_url.strip():
        return normalize_url(explicit_url)

    settings = load_settings(config_path=config_path)
    return settings.get("pocketbase_url", DEFAULT_POCKETBASE_URL)


def resolve_server_url(
    explicit_url: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    """Resolve Appium Server URL according to precedence hierarchy:

    1. Explicit programmatic/CLI argument (`explicit_url`)
    2. Environment overrides and configuration files via Configuration Realm
    3. Fallback default (`http://127.0.0.1:4723`)
    """
    if explicit_url and explicit_url.strip():
        return normalize_url(explicit_url)

    settings = load_settings(config_path=config_path)
    return settings.get("server_url", DEFAULT_SERVER_URL)


def resolve_git_common_root(cwd: str | Path | None = None) -> Path:
    """Resolve the canonical root directory of the repository, accounting for Git worktrees.

    If inside a Git worktree, this returns the root directory containing the common .git directory.
    If outside a Git repository, it falls back to the current working directory.
    """
    env_root = os.getenv("BOSS_CONFIG_ROOT")
    if env_root and env_root.strip():
        return Path(env_root.strip()).resolve()

    import subprocess

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
        )
        common_git = Path(res.stdout.strip())
        if not common_git.is_absolute():
            base = Path(cwd) if cwd else Path.cwd()
            common_git = (base / common_git).resolve()
        return common_git.parent
    except Exception:
        return Path(cwd).resolve() if cwd else Path.cwd()


def resolve_pocketbase_db_path(
    explicit_path: str | Path | None = None,
    config_path: str | Path | None = None,
    resolve_common_root: bool = False,
) -> Path:
    """Resolve PocketBase SQLite database (data.db) path according to precedence hierarchy:

    1. Explicit programmatic/CLI argument (`explicit_path`)
    2. `PB_DB_PATH` or `POCKETBASE_DB_PATH` environment variable
    3. `PB_DATA_DIR` or `POCKETBASE_DATA_DIR` environment variable (+ /data.db)
    4. Configuration files (`pocketbase_db_path` or `pb_db_path`)
    5. Configuration files (`pocketbase_data_dir` or `pb_data_dir` + /data.db)
    6. Fallback default (`.boss_agent/pb_data/data.db`)
    """
    if explicit_path:
        p = Path(explicit_path)
        if p.is_dir() or (not p.suffix and p.name != "data.db"):
            return p / "data.db"
        return p

    env_db = config_realm.resolve_env_override("pocketbase_db_path")
    if env_db is not None:
        p = Path(env_db)
        return p / "data.db" if (p.is_dir() or not p.suffix) else p

    env_data_dir = config_realm.resolve_env_override("pocketbase_data_dir")
    if env_data_dir is not None:
        return Path(env_data_dir) / "data.db"

    settings = load_settings(config_path=config_path)
    if settings.get("pocketbase_db_path"):
        p = Path(settings["pocketbase_db_path"])
        p = p / "data.db" if (p.is_dir() or not p.suffix) else p
        if resolve_common_root and not p.is_absolute():
            return resolve_git_common_root() / p
        return p

    if settings.get("pocketbase_data_dir"):
        p = Path(settings["pocketbase_data_dir"]) / "data.db"
        if resolve_common_root and not p.is_absolute():
            return resolve_git_common_root() / p
        return p

    fallback = Path(DEFAULT_POCKETBASE_DB_PATH)
    if resolve_common_root:
        return resolve_git_common_root() / fallback
    return fallback


def resolve_pocketbase_data_dir(
    explicit_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    resolve_common_root: bool = False,
) -> Path:
    """Resolve PocketBase data directory according to precedence hierarchy:

    1. Explicit programmatic/CLI argument (`explicit_dir`)
    2. `PB_DATA_DIR` or `POCKETBASE_DATA_DIR` environment variable
    3. Configuration files (`pocketbase_data_dir` or `pb_data_dir`)
    4. Configuration files (`pocketbase_db_path` or `pb_db_path` parent directory)
    5. Fallback default (`.boss_agent/pb_data`)
    """
    if explicit_dir:
        return Path(explicit_dir)

    env_data_dir = config_realm.resolve_env_override("pocketbase_data_dir")
    if env_data_dir is not None:
        return Path(env_data_dir)

    settings = load_settings(config_path=config_path)
    if settings.get("pocketbase_data_dir"):
        p = Path(settings["pocketbase_data_dir"])
        if resolve_common_root and not p.is_absolute():
            return resolve_git_common_root() / p
        return p

    if settings.get("pocketbase_db_path"):
        p = Path(settings["pocketbase_db_path"]).parent
        if resolve_common_root and not p.is_absolute():
            return resolve_git_common_root() / p
        return p

    fallback = Path(DEFAULT_POCKETBASE_DATA_DIR)
    if resolve_common_root:
        return resolve_git_common_root() / fallback
    return fallback


def resolve_communication_cooldown_days(overrides: dict[str, Any] | None = None) -> int:
    """Resolve the re-application cool-down window in days.

    Task payload overrides win over system settings; 0 explicitly means permanent suppression
    and must never be confused with "unset".
    """
    from boss_agent.core.shared.keyword_constants import DEFAULT_COMMUNICATION_COOLDOWN_DAYS

    raw = (overrides or {}).get("communication_cooldown_days")
    if raw is None:
        raw = load_settings().get("communication_cooldown_days")
    if raw is None:
        return DEFAULT_COMMUNICATION_COOLDOWN_DAYS
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid communication_cooldown_days value %r; falling back to default %d",
            raw,
            DEFAULT_COMMUNICATION_COOLDOWN_DAYS,
        )
        return DEFAULT_COMMUNICATION_COOLDOWN_DAYS


def load_feed_config_defaults() -> dict[str, Any]:
    """The operator-resolved defaults a feed task payload is overlaid on.

    The feed parser is pure (``FeedStreamConfig.from_payload``); resolving the global
    Screening Policy, the greeting ceiling and the cool-down window is the edge's job,
    and this is the one place the task handlers ask for them.
    """
    from boss_agent.adapters.outbound.config.screening_config import load_screening_policy

    settings = load_settings()
    return {
        "default_policy": load_screening_policy(),
        "default_daily_greeting_limit": int(settings.get("daily_greeting_limit", 20)),
        "default_cooldown_days": resolve_communication_cooldown_days(),
    }


def load_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    """The merged Configuration Realm, from the realm's one chain and defaults table.

    Cached against the chain's stat signature: `WorkerConfig` construction alone used
    to trigger three full read-and-parse passes over the same files.
    """
    return config_realm.load_settings(config_path=config_path)
