"""
boss_agent.config_realm
=======================
The one loader, precedence chain and defaults table for the Configuration Realm.

The realm (`config/*.yaml`) is the declared single source of truth, but it used to be
read through five independent loaders with four different precedence orders and three
different default tables. The drift that produced was not academic: the LLM client
loader started from the *example* template and shipped `max_tokens=16384` /
`timeout_sec=300.0` while every other realm shipped `262144` / `120.0`, so a
config-less LLM call silently behaved unlike the documented template.

What lives here, once:

* :data:`CONFIG_CHAIN` — the ordered file chain, lowest precedence first;
* :data:`DEFAULTS` — the one defaults table, plus :data:`CHAT_DEFAULTS` for the
  nested `chat:` block (ADR 0010);
* :data:`ENV_OVERRIDES` — the fixed key set the environment can raise above files;
* :func:`is_mask_placeholder` — the one masked-secret predicate;
* :func:`load_settings` — the one merge engine, cached against the chain's own
  stat signature;
* typed accessors (:class:`WorkerSettings`, :class:`LlmSettings`,
  :class:`PocketBaseSettings`) so readers ask for a section instead of re-deriving
  it from raw keys.

The compatibility knobs `settings.DEFAULT_CONFIG_SEARCH_PATHS` and `settings.yaml`
are read *late*, from the module, because both are long-standing test and embedder
seams. They must keep working; they just no longer imply a second loader.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

try:
    import yaml
except ImportError:  # pragma: no cover - the flat fallback parser covers this
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger("boss_agent.config_realm")

DEFAULT_POCKETBASE_URL: str = "http://127.0.0.1:8090"
DEFAULT_POCKETBASE_DATA_DIR: str = ".boss_agent/pb_data"
DEFAULT_POCKETBASE_DB_PATH: str = ".boss_agent/pb_data/data.db"
DEFAULT_SERVER_URL: str = "http://127.0.0.1:4723"

#: The ordered file chain, **highest precedence first** — the order the loader has
#: always been fed and the one `settings.DEFAULT_CONFIG_SEARCH_PATHS` is written in.
#: The merge walks it reversed, so the last entry is the floor.
#:
#: `settings.example.yaml` is that floor deliberately: it is the shipped template, so a
#: run with no local file behaves like the documented template rather than like a
#: second, private default set — which is exactly how the LLM loader drifted.
CONFIG_CHAIN: tuple[Path, ...] = (
    Path("config/settings.local.yaml"),
    Path("config/settings.local.json"),
    Path("config/settings.yaml"),
    Path("config/settings.example.yaml"),
)

#: The historical test/embedder seam. Kept as a module attribute so a rebinding is
#: honoured late by :func:`_compat_attr`; the values themselves live in CONFIG_CHAIN.
DEFAULT_CONFIG_SEARCH_PATHS: list[Path] = list(CONFIG_CHAIN)

#: Legacy file, consulted only when the chain produced no usable API key. It predates
#: the realm and is kept as an explicit chain entry rather than as a hidden second pass.
LEGACY_LLM_FILE: Path = Path("config/llm.local.yaml")

#: The keys that make a file a Screening Policy store. One list, so the merged view and
#: :func:`load_screening_policy` cannot disagree about which file is "the screening one".
SCREENING_KEYS: tuple[str, ...] = (
    "enable_screening",
    "title_blacklist",
    "title_whitelist",
    "jd_blacklist",
    "company_blacklist",
    "channel_preference",
    "max_commute_distance_km",
)

#: Pre-realm Screening Policy files, consulted only when no file in the chain states a
#: screening key. They are a rescue pass of the whole-realm loader, so the dashboard and
#: the worker read one policy (ADR 0021 §1).
LEGACY_SCREENING_FILES: tuple[Path, ...] = (
    Path("config/screening.local.yaml"),
    Path("config/screening.local.yml"),
    Path("config/screening.local.json"),
    Path("config/screening.yaml"),
    Path("config/screening.example.yaml"),
)

#: The wire protocols this realm resolves `provider` to — OpenAI Chat Completions and
#: Anthropic Messages. A vendor is *not* a protocol: it is chosen by `base_url` and
#: `model`. Values predating protocol-keyed providers (`minimax`, `deepseek`) name a
#: model house that speaks the default protocol, so they converge rather than fail.
LLM_PROTOCOLS: tuple[str, ...] = ("openai", "anthropic")

#: The protocol a `provider` lands on when it names none this realm speaks.
DEFAULT_LLM_PROTOCOL: str = "openai"


def normalize_provider(provider: Any) -> str:
    """Resolve a configured ``provider`` to one of :data:`LLM_PROTOCOLS`.

    Normalizing here rather than rejecting keeps an existing config working: a user
    whose file still says ``provider: minimax`` gets a working client instead of a
    crash on startup. The web dashboard applies the same rule at its own boundary
    (``normalizeLlmProvider`` in ``web/src/lib/types.ts``), so both halves of the
    console agree on what an unrecognized value means.
    """
    if not isinstance(provider, str):
        return DEFAULT_LLM_PROTOCOL
    resolved = provider.strip().lower()
    return resolved if resolved in LLM_PROTOCOLS else DEFAULT_LLM_PROTOCOL


#: The one defaults table. Every reader resolves its baseline from here; the Web layer
#: consumes the merged view, so there is no second table to drift.
DEFAULTS: dict[str, Any] = {
    "device": "emulator-5554",
    "avd_name": "boss_avd_arm64",
    "server_url": DEFAULT_SERVER_URL,
    "pocketbase_url": DEFAULT_POCKETBASE_URL,
    "pocketbase_data_dir": None,
    "pocketbase_db_path": None,
    "provider": "openai",
    "base_url": "https://api.minimaxi.com/v1",
    "api_key": None,
    "model": "MiniMax-M3",
    "temperature": 0.2,
    "timeout_sec": 120.0,
    "max_tokens": 262144,
    "langsmith_tracing": False,
    "langsmith_api_key": None,
    "langsmith_project": "boss-agent-mobile",
    "daily_greeting_limit": 20,
    "preview_timeout_sec": 3.0,
    "enable_greeting": True,
    "communication_cooldown_days": 30,
    # Screening switches live in the same table as every other realm default.
    "enable_screening": True,
    "channel_preference": "all",
    "run_cleanup_on_startup": True,
    "title_whitelist": [],
    "title_blacklist": [
        "销售",
        "电话销售",
        "电销",
        "管培生",
        "实习",
        "助理",
        "讲师",
        "课程顾问",
        "客服",
    ],
    "company_blacklist": [],
    "jd_blacklist": ["驻场", "外包", "电销", "无底薪", "纯提成"],
    "business_district_blacklist": [],
    "business_district_inspect_list": [],
    "max_commute_distance_km": 40.0,
}


def _chat_defaults() -> dict[str, Any]:
    """The nested ``chat:`` block's defaults (ADR 0010).

    Kept separate from :data:`DEFAULTS` because the block is deep-merged per key rather
    than replaced, so one key's default cannot mask another's. The values are the
    rejection module's own constants rather than a second copy of them.
    """
    from .rejection import (
        DEFAULT_MAX_SCAN_DEPTH,
        DEFAULT_MAX_SCROLL_SWIPES,
        DEFAULT_REJECTION_REPLY_TEXT,
    )

    return {
        "rejection_reply_text": DEFAULT_REJECTION_REPLY_TEXT,
        "max_scan_depth": DEFAULT_MAX_SCAN_DEPTH,
        "max_scroll_swipes": DEFAULT_MAX_SCROLL_SWIPES,
        "dry_run": False,
    }


CHAT_DEFAULTS: dict[str, Any] = _chat_defaults()

#: Characters a masked secret is rendered with. A display value must never be ingested
#: as a real key — it would authenticate as the literal mask and fail every call.
MASK_MARKERS: tuple[str, ...] = ("•", "****")


def normalize_url(url: str) -> str:
    """Normalize URL by stripping surrounding whitespace and trailing slashes."""
    return url.strip().rstrip("/")


def is_mask_placeholder(value: Any) -> bool:
    """Whether ``value`` is a masked display string rather than a usable secret.

    One predicate for every Python loader and script; the Web side's
    `isMaskedDisplayValue` is its mirror and is pinned by the same fixture.
    """
    if value is None:
        return False
    text = str(value)
    return any(marker in text for marker in MASK_MARKERS)


# --------------------------------------------------------------------------- #
# The loader
# --------------------------------------------------------------------------- #


def _compat_attr(name: str, fallback: Any) -> Any:
    """Read a compatibility knob late.

    `DEFAULT_CONFIG_SEARCH_PATHS` and `yaml` are rebound by tests and embedders; the
    lookup has to happen at call time or rebinding them would silently stop working.
    """
    return getattr(sys.modules[__name__], name, fallback)


def resolve_config_root() -> Path:
    return resolve_git_common_root()


def get_legacy_llm_file() -> Path:
    override = os.getenv("BOSS_LEGACY_LLM_PATH")
    if override and override.strip():
        return Path(override.strip())
    root = resolve_config_root()
    p = LEGACY_LLM_FILE
    if (Path.cwd() / p).is_file():
        return Path.cwd() / p
    if (root / p).is_file():
        return root / p
    return root / p


def resolve_chain(config_path: str | Path | None = None) -> list[Path]:
    """The ordered file chain (highest precedence first) for one load.

    An explicit `config_path` short-circuits the chain to that one file; that is the
    tmp-YAML seam every config test uses.
    """
    if config_path:
        return [Path(config_path)]
    custom = _compat_attr("DEFAULT_CONFIG_SEARCH_PATHS", None)
    if custom is not None and list(custom) != list(CONFIG_CHAIN):
        return list(custom)

    local_override = os.getenv("BOSS_SETTINGS_LOCAL_PATH")
    config_root_env = os.getenv("BOSS_CONFIG_ROOT")

    if not local_override and not config_root_env:
        return list(CONFIG_CHAIN)

    chain: list[Path] = []
    root = resolve_config_root() if config_root_env else None

    for p in CONFIG_CHAIN:
        if p.name == "settings.local.yaml" and local_override and local_override.strip():
            chain.append(Path(local_override.strip()))
        elif root is not None and not p.is_absolute():
            chain.append(root / p)
        else:
            chain.append(p)
    return chain


def _env_signature() -> tuple:
    """The override-relevant environment, as part of the cache key.

    The environment sits above the files, so a cached load under one environment is not
    valid under another. Reading a dozen variables is far cheaper than re-parsing YAML,
    and omitting them would make the cache answer a question it was not asked.
    """
    overrides = tuple(
        (key, tuple((name, os.getenv(name)) for name in names)) for key, names in ENV_OVERRIDES
    )
    special_vars = (
        ("BOSS_SETTINGS_LOCAL_PATH", os.getenv("BOSS_SETTINGS_LOCAL_PATH")),
        ("BOSS_LEGACY_LLM_PATH", os.getenv("BOSS_LEGACY_LLM_PATH")),
        ("BOSS_CONFIG_ROOT", os.getenv("BOSS_CONFIG_ROOT")),
        ("CHAT_REJECTION_REPLY_TEXT", os.getenv("CHAT_REJECTION_REPLY_TEXT")),
        ("CHAT_MAX_SCAN_DEPTH", os.getenv("CHAT_MAX_SCAN_DEPTH")),
        ("CHAT_MAX_SCROLL_SWIPES", os.getenv("CHAT_MAX_SCROLL_SWIPES")),
        ("CHAT_DRY_RUN", os.getenv("CHAT_DRY_RUN")),
        ("RUN_CLEANUP_ON_STARTUP", os.getenv("RUN_CLEANUP_ON_STARTUP")),
    )
    return (overrides, special_vars)


def _legacy_signature() -> tuple:
    """The legacy rescue files' stats, as part of the cache key.

    The LLM file is resolved *relative to the working directory*, so two loads can share
    a chain and an environment yet read different files. Leaving it out of the key made
    the cache answer with a value read from a different directory entirely. The legacy
    screening files are candidates of a rescue pass, so an edit to any of them must also
    invalidate the merged view.
    """
    root = resolve_config_root()
    return _chain_signature(
        [get_legacy_llm_file(), *(root / rel for rel in LEGACY_SCREENING_FILES)]
    )


def _chain_signature(chain: list[Path]) -> tuple:
    """A cheap identity for the chain's current contents.

    Stat rather than read: a settings read happens per task and per accessor, and the
    expensive part is parsing, not asking the filesystem whether a file changed. The
    stat is what makes the cache safe against a live Settings Panel edit.
    """
    signature: list[tuple] = []
    for path in chain:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None, None))
    return tuple(signature)


def _parse_file(path: Path) -> dict[str, Any] | None:
    """Parse one config file, or None when it is absent or unreadable."""
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        if path.suffix in (".yaml", ".yml"):
            yaml = _compat_attr("yaml", None)
            loaded = yaml.safe_load(content) if yaml is not None else _parse_yaml_fallback(content)
        else:
            loaded = json.loads(content)
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def _parse_yaml_fallback(content: str) -> dict[str, Any]:
    """A minimal flat-YAML reader for when PyYAML is unavailable.

    Deliberately flat: it exists so a missing optional dependency degrades to
    "top-level scalar keys only", not to "no configuration at all".
    """
    loaded: dict[str, Any] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip()
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        elif "\t#" in value:
            value = value.split("\t#", 1)[0].strip()
        loaded[key.strip()] = value.strip('"').strip("'")
    return loaded


#: Aliases folded onto their canonical key. Declared once so a legacy spelling is
#: visible rather than handled per loader.
KEY_ALIASES: dict[str, str] = {
    "pb_url": "pocketbase_url",
    "appium_url": "server_url",
    "pb_data_dir": "pocketbase_data_dir",
    "pb_db_path": "pocketbase_db_path",
}

#: Keys that are deep-merged per key instead of replaced. The Web writer merges the same
#: way, which is what keeps a partial local override from dropping a sibling (ADR 0010).
DEEP_MERGED_BLOCKS: tuple[str, ...] = ("chat",)

#: The environment override table: (canonical key, env var names in precedence order).
#: The environment sits above every file; the key set is fixed and lives here so it is
#: visible in one place rather than scattered through the loader's tail.
ENV_OVERRIDES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pocketbase_url", ("POCKETBASE_URL",)),
    ("server_url", ("APPIUM_SERVER_URL", "APPIUM_URL")),
    ("pocketbase_data_dir", ("PB_DATA_DIR", "POCKETBASE_DATA_DIR")),
    ("pocketbase_db_path", ("PB_DB_PATH", "POCKETBASE_DB_PATH")),
    ("api_key", ("LLM_API_KEY", "MINIMAX_API_KEY", "OPENAI_API_KEY")),
    ("base_url", ("LLM_BASE_URL", "MINIMAX_BASE_URL")),
    ("model", ("LLM_MODEL",)),
    ("avd_name", ("ANDROID_AVD",)),
    ("run_cleanup_on_startup", ("RUN_CLEANUP_ON_STARTUP",)),
)

#: Legacy LLM keys rescued from :data:`LEGACY_LLM_FILE` when the chain produced no key.
LEGACY_LLM_KEYS: tuple[str, ...] = (
    "provider",
    "base_url",
    "api_key",
    "model",
    "temperature",
    "timeout_sec",
    "max_tokens",
    "langsmith_tracing",
    "langsmith_api_key",
    "langsmith_project",
)

#: The template's placeholder API key: a doc value, not a secret.
PLACEHOLDER_API_KEY: str = "your-api-key-here"

_cache: dict[tuple, dict[str, Any]] = {}


def invalidate_cache() -> None:
    """Drop every cached load. The explicit seam for tests and for a writer that
    just persisted settings from this process."""
    _cache.clear()


def _apply_file(merged: dict[str, Any], loaded: dict[str, Any]) -> None:
    """Fold one parsed file into the accumulator, highest-precedence wins."""
    for key, value in loaded.items():
        if value is None:
            if key == "max_commute_distance_km":
                merged[key] = None
            continue
        if key == "api_key" and (value == PLACEHOLDER_API_KEY or is_mask_placeholder(value)):
            # A masked display value or the template's placeholder is not a secret. No
            # chain may ingest one: it would authenticate as the literal mask.
            continue
        if key in KEY_ALIASES:
            merged[KEY_ALIASES[key]] = value
            continue
        if key in DEEP_MERGED_BLOCKS and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
            continue
        merged[key] = value


def _apply_legacy_llm_file(merged: dict[str, Any]) -> None:
    """Rescue LLM keys from the pre-realm file when the chain yielded no usable key."""
    if merged.get("api_key") not in (None, "", PLACEHOLDER_API_KEY):
        return
    loaded = _parse_file(get_legacy_llm_file())
    if not loaded:
        return
    for key in LEGACY_LLM_KEYS:
        value = loaded.get(key)
        if value is None:
            continue
        if key == "api_key" and value == PLACEHOLDER_API_KEY:
            continue
        merged[key] = value


def _screening_source_file() -> Path | None:
    """The file that states the active Screening Policy: chain first, then legacy."""
    for path in resolve_chain():
        data = _parse_file(path) or {}
        if any(key in data for key in SCREENING_KEYS):
            return path
    root = resolve_config_root()
    for rel in LEGACY_SCREENING_FILES:
        candidate = root / rel
        data = _parse_file(candidate) or {}
        if any(key in data for key in SCREENING_KEYS):
            return candidate
    return None


def _apply_legacy_screening_file(merged: dict[str, Any], chain: list[Path]) -> None:
    """Rescue screening keys from a pre-realm file when no chain file states any.

    The worker used to apply this fallback inside :func:`load_screening_policy` alone,
    while the dashboard projected the merged view — so an operator with
    ``config/screening.local.yaml`` saw realm defaults in the dashboard and had the
    legacy rules enforced on the phone (ADR 0021 §1).
    """
    for path in chain:
        data = _parse_file(path) or {}
        if any(key in data for key in SCREENING_KEYS):
            return
    root = resolve_config_root()
    for rel in LEGACY_SCREENING_FILES:
        data = _parse_file(root / rel) or {}
        stated = {key: value for key, value in data.items() if key in SCREENING_KEYS}
        if stated:
            _apply_file(merged, stated)
            return


def _apply_env_overrides(merged: dict[str, Any]) -> None:
    for key, env_names in ENV_OVERRIDES:
        for env_name in env_names:
            value = os.getenv(env_name)
            if value and value.strip():
                merged[key] = value.strip()
                break


def _apply_chat_env_overrides(merged: dict[str, Any]) -> None:
    if not isinstance(merged.get("chat"), dict):
        merged["chat"] = dict(CHAT_DEFAULTS)
    chat = merged["chat"]
    reply_text = os.getenv("CHAT_REJECTION_REPLY_TEXT")
    if reply_text and reply_text.strip():
        chat["rejection_reply_text"] = reply_text.strip()
    scan_depth = os.getenv("CHAT_MAX_SCAN_DEPTH")
    if scan_depth and scan_depth.strip():
        chat["max_scan_depth"] = scan_depth.strip()
    scroll_swipes = os.getenv("CHAT_MAX_SCROLL_SWIPES")
    if scroll_swipes and scroll_swipes.strip():
        chat["max_scroll_swipes"] = scroll_swipes.strip()
    dry_run = os.getenv("CHAT_DRY_RUN")
    if dry_run and dry_run.strip():
        chat["dry_run"] = dry_run.strip()


def _coerce_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


def _coerce_scalar_types(merged: dict[str, Any]) -> None:
    # A malformed scalar falls back to the realm default instead of surviving as a string:
    # every reader of these keys is typed as a number (the dashboard binds
    # ``temperature`` / ``max_tokens`` / ``daily_greeting_limit`` straight into inputs).
    for int_key in ("max_tokens", "daily_greeting_limit", "communication_cooldown_days"):
        if int_key in merged and merged[int_key] is not None:
            try:
                merged[int_key] = int(merged[int_key])
            except (ValueError, TypeError):
                merged[int_key] = DEFAULTS[int_key]

    for float_key in ("temperature", "timeout_sec", "preview_timeout_sec"):
        if float_key in merged and merged[float_key] is not None:
            try:
                merged[float_key] = float(merged[float_key])
            except (ValueError, TypeError):
                merged[float_key] = DEFAULTS[float_key]

    if "max_commute_distance_km" in merged:
        val = merged["max_commute_distance_km"]
        if val is None or val == "" or str(val).lower() in ("null", "none"):
            merged["max_commute_distance_km"] = None
        else:
            try:
                num = float(val)
                merged["max_commute_distance_km"] = num if num > 0 else None
            except (ValueError, TypeError):
                merged["max_commute_distance_km"] = None

    for bool_key in (
        "langsmith_tracing",
        "enable_greeting",
        "enable_screening",
        "run_cleanup_on_startup",
    ):
        if bool_key in merged and merged[bool_key] is not None:
            merged[bool_key] = _coerce_bool(merged[bool_key])

    for list_key in (
        "title_whitelist",
        "title_blacklist",
        "company_blacklist",
        "jd_blacklist",
        "business_district_blacklist",
        "business_district_inspect_list",
    ):
        if list_key in merged:
            val = merged[list_key]
            if isinstance(val, str):
                val_stripped = val.strip()
                if val_stripped.startswith("[") and val_stripped.endswith("]"):
                    try:
                        merged[list_key] = json.loads(val_stripped)
                    except Exception:
                        merged[list_key] = [
                            s.strip().strip('"').strip("'")
                            for s in val_stripped[1:-1].split(",")
                            if s.strip()
                        ]
                elif not val_stripped:
                    merged[list_key] = []
                else:
                    merged[list_key] = [val_stripped]
            elif not isinstance(val, list):
                merged[list_key] = []

    if isinstance(merged.get("chat"), dict):
        chat = merged["chat"]
        for int_key in ("max_scan_depth", "max_scroll_swipes"):
            if int_key in chat and chat[int_key] is not None:
                with contextlib.suppress(ValueError, TypeError):
                    chat[int_key] = int(chat[int_key])
        if "dry_run" in chat and chat["dry_run"] is not None:
            chat["dry_run"] = _coerce_bool(chat["dry_run"])


def _derive_paths(merged: dict[str, Any]) -> None:
    """Keep `pocketbase_data_dir` and `pocketbase_db_path` consistent.

    Either may be the one that was specified; the other is derived so no reader has to
    know which spelling the operator happened to use.
    """
    data_dir = merged.get("pocketbase_data_dir")
    db_path = merged.get("pocketbase_db_path")
    if data_dir and not db_path:
        merged["pocketbase_db_path"] = str(Path(data_dir) / "data.db")
    elif db_path and not data_dir:
        merged["pocketbase_data_dir"] = str(Path(db_path).parent)
    elif not data_dir and not db_path:
        merged["pocketbase_data_dir"] = DEFAULT_POCKETBASE_DATA_DIR
        merged["pocketbase_db_path"] = DEFAULT_POCKETBASE_DB_PATH


def _load_uncached(chain: list[Path], *, include_legacy_fallback: bool) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(DEFAULTS)
    merged["chat"] = dict(CHAT_DEFAULTS)

    for path in reversed(chain):  # lowest precedence first
        loaded = _parse_file(path)
        if loaded:
            _apply_file(merged, loaded)

    if include_legacy_fallback:
        _apply_legacy_llm_file(merged)
        _apply_legacy_screening_file(merged, chain)

    _apply_env_overrides(merged)
    _apply_chat_env_overrides(merged)
    _derive_paths(merged)
    _coerce_scalar_types(merged)

    for key in ("pocketbase_url", "server_url"):
        if isinstance(merged.get(key), str):
            merged[key] = normalize_url(merged[key])

    return merged


def load_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    """The merged Configuration Realm, from the one chain and the one defaults table.

    Cached against the chain's stat signature, so repeated resolution within a run —
    `WorkerConfig` alone used to trigger three full loads — costs a stat per file
    rather than a read-and-parse, while an edit on disk (the Settings Panel writing
    `settings.local.yaml`) still takes effect on the next read.
    """
    return load_chain(
        resolve_chain(config_path),
        config_path=config_path,
        include_legacy_fallback=config_path is None,
    )


def load_chain(
    chain: list[Path],
    *,
    config_path: str | Path | None = None,
    include_legacy_fallback: bool = False,
) -> dict[str, Any]:
    """Merge an explicit ordered chain (highest precedence first) over the defaults.

    For a realm that carries historical files of its own — the LLM client reads two
    pre-realm files that sit just above the shipped template. Such a loader declares
    its chain and shares this engine, rather than running a second merge with its own
    defaults, which is how `max_tokens` came to differ by a factor of sixteen.

    ``include_legacy_fallback`` is the *whole-realm* loader's rescue pass for the
    pre-realm `config/llm.local.yaml`, applied last and only when it contributed
    nothing already. A realm that lists legacy files in its own chain must leave this
    off: re-applying them here would silently promote them above every other file.
    """
    signature = (
        config_path,
        _chain_signature(chain),
        _legacy_signature(),
        _env_signature(),
        include_legacy_fallback,
    )
    cached = _cache.get(signature)
    if cached is None:
        cached = _load_uncached(chain, include_legacy_fallback=include_legacy_fallback)
        _cache[signature] = cached
    return copy.deepcopy(cached)


# --------------------------------------------------------------------------- #
# The patch engine: one round-trip writer for every Configuration Realm store
# --------------------------------------------------------------------------- #

#: Keys whose value is a secret. A masked display value or the shipped template's
#: placeholder must never be persisted as a real key (issue #185).
SECRET_KEYS: frozenset[str] = frozenset({"api_key", "langsmith_api_key"})

#: Display/placeholder secrets that must never be written back. The mask glyphs are
#: covered by :func:`is_mask_placeholder`; these are the example template's literal docs.
PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {"your-api-key-here", "your-langsmith-api-key-here"}
)


def resolve_writable_settings_path(config_path: str | Path | None = None) -> Path:
    """The highest-precedence writable file: the ``settings.local.yaml`` target.

    Honours the sandbox seams exactly like the loader (``BOSS_SETTINGS_LOCAL_PATH``,
    ``BOSS_CONFIG_ROOT``), so a test patch and a test read see the same file.
    """
    if config_path:
        return Path(config_path)
    return resolve_chain()[0]


def _roundtrip_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _load_roundtrip(path: Path) -> CommentedMap:
    """Load a YAML store without discarding comments, or an empty mapping.

    A document that exists but is not a mapping is refused, not replaced: patching it
    would silently discard the operator's file.
    """
    yaml = _roundtrip_yaml()
    if not path.is_file():
        return CommentedMap()
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.load(handle)
    if loaded is None:
        return CommentedMap()
    if not isinstance(loaded, CommentedMap):
        raise ValueError(f"{path} is not a YAML mapping; refusing to patch it")
    return loaded


def _write_roundtrip(path: Path, data: dict[str, Any]) -> None:
    """Write the store atomically, following a symlink instead of replacing it.

    The file on disk is often a symlink into the shared checkout, and an in-place ``"w"``
    truncates the operator's config before the dump has succeeded.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    yaml = _roundtrip_yaml()
    handle_fd, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            yaml.dump(data, handle)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _reject_secret(key: str, value: Any) -> bool:
    """True when ``value`` must not be persisted for a secret key.

    An empty string is a valid "clear the secret" statement only when the caller means
    it; the Web layer already resolves real-vs-display keys before patching, so here we
    simply refuse anything that is not a usable secret.
    """
    if key not in SECRET_KEYS:
        return False
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    text = str(value)
    return text in PLACEHOLDER_SECRETS or is_mask_placeholder(text)


def _merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if _reject_secret(key, value):
            continue
        if key in DEEP_MERGED_BLOCKS and isinstance(value, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                target[key] = CommentedMap()
                existing = target[key]
            _merge_patch(existing, value)
            continue
        target[key] = value


def patch_settings(
    patch: dict[str, Any],
    *,
    config_path: str | Path | None = None,
) -> Path:
    """Apply a partial settings object to the writable local store.

    Only keys the patch states change; every other key, comment and section ordering
    survives the round-trip. The nested ``chat`` block deep-merges. Masked display
    values and template placeholders are never written as secrets.
    """
    if not isinstance(patch, dict):
        raise TypeError("settings patch must be a JSON object")
    target = resolve_writable_settings_path(config_path)
    data = _load_roundtrip(target)
    _merge_patch(data, patch)
    _write_roundtrip(target, data)
    invalidate_cache()
    return target


def read_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """The merged view the Web Dashboard consumes: settings plus screening policy.

    One loader, so the dashboard and the worker cannot disagree about the active
    policy. The screening view is the merged settings projected onto the policy keys.
    """
    from .screening_policy import ScreeningPolicy

    merged = load_settings(config_path=config_path)
    return {
        "settings": merged,
        "screening_policy": ScreeningPolicy.from_dict(merged).to_dict(),
    }


def append_company_blacklist_entry(
    company_name: str,
    path: str | Path | None = None,
    policy: Any = None,
) -> bool:
    """Append one company to ``company_blacklist`` through the round-trip engine.

    Idempotent and comment-preserving: returns True only when the file changed.
    ``policy`` supplies the full snapshot used when the target file does not exist.
    """
    cleaned = (company_name or "").strip()
    if not cleaned:
        return False

    target = Path(path) if path else resolve_writable_screening_config_path()
    if target.suffix == ".json":
        # The realm's writable store is YAML; a JSON layer has no comment-preserving
        # round-trip and sits above the YAML target, so writing here would be inert.
        return False

    if not target.is_file():
        from .screening_policy import ScreeningPolicy

        snapshot = (policy or ScreeningPolicy.load_default()).to_dict()
        blacklist = [str(item) for item in snapshot.get("company_blacklist", [])]
        if cleaned not in blacklist:
            snapshot["company_blacklist"] = [*blacklist, cleaned]
        patch_settings(snapshot, config_path=target)
        return True

    data = _load_roundtrip(target)
    existing = data.get("company_blacklist")
    if existing is None:
        data["company_blacklist"] = CommentedSeq([cleaned])
    elif not isinstance(existing, list) or cleaned in [str(item) for item in existing]:
        return False
    else:
        existing.append(cleaned)
    _write_roundtrip(target, data)
    invalidate_cache()
    return True


def save_screening_policy(policy: Any, config_path: str | Path | None = None) -> Path:
    """Persist a ScreeningPolicy through the one patch engine."""
    return patch_settings(policy.to_dict(), config_path=config_path)


# --------------------------------------------------------------------------- #
# Path, cooldown and acknowledgment resolvers
# --------------------------------------------------------------------------- #


def resolve_git_common_root(cwd: str | Path | None = None) -> Path:
    """The canonical repository root, accounting for Git worktrees."""
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


def resolve_pocketbase_url(
    explicit_url: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    """PocketBase URL: explicit arg, then environment, then files, then default."""
    if explicit_url and explicit_url.strip():
        return normalize_url(explicit_url)

    env_url = os.getenv("POCKETBASE_URL")
    if env_url and env_url.strip():
        return normalize_url(env_url)

    return load_settings(config_path=config_path).get("pocketbase_url", DEFAULT_POCKETBASE_URL)


def resolve_server_url(
    explicit_url: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    """Appium Server URL: explicit arg, then environment, then files, then default."""
    if explicit_url and explicit_url.strip():
        return normalize_url(explicit_url)

    env_url = os.getenv("APPIUM_SERVER_URL") or os.getenv("APPIUM_URL")
    if env_url and env_url.strip():
        return normalize_url(env_url)

    return load_settings(config_path=config_path).get("server_url", DEFAULT_SERVER_URL)


def resolve_pocketbase_db_path(
    explicit_path: str | Path | None = None,
    config_path: str | Path | None = None,
    resolve_common_root: bool = False,
) -> Path:
    """Resolve the PocketBase SQLite ``data.db`` path."""
    if explicit_path:
        p = Path(explicit_path)
        if p.is_dir() or (not p.suffix and p.name != "data.db"):
            return p / "data.db"
        return p

    env_db = os.getenv("PB_DB_PATH") or os.getenv("POCKETBASE_DB_PATH")
    if env_db and env_db.strip():
        p = Path(env_db.strip())
        return p / "data.db" if (p.is_dir() or not p.suffix) else p

    env_data_dir = os.getenv("PB_DATA_DIR") or os.getenv("POCKETBASE_DATA_DIR")
    if env_data_dir and env_data_dir.strip():
        return Path(env_data_dir.strip()) / "data.db"

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
    """Resolve the PocketBase data directory."""
    if explicit_dir:
        return Path(explicit_dir)

    env_data_dir = os.getenv("PB_DATA_DIR") or os.getenv("POCKETBASE_DATA_DIR")
    if env_data_dir and env_data_dir.strip():
        return Path(env_data_dir.strip())

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
    """The re-application cool-down window in days; 0 means permanent suppression."""
    from .keyword_constants import DEFAULT_COMMUNICATION_COOLDOWN_DAYS

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


def resolve_chat_acknowledgment_settings(
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
):
    """Resolve the rejection triage settings (env, then nested ``chat``, then defaults)."""
    from .rejection import (
        DEFAULT_MAX_SCAN_DEPTH,
        DEFAULT_MAX_SCROLL_SWIPES,
        DEFAULT_REJECTION_REPLY_TEXT,
        ChatAcknowledgmentSettings,
        coerce_bool,
        coerce_positive_int,
    )

    merged = settings if settings is not None else load_settings(config_path=config_path)
    chat_block = merged.get("chat")
    chat: dict[str, Any] = chat_block if isinstance(chat_block, dict) else {}

    reply_text = chat.get("rejection_reply_text")
    scan_depth = chat.get("max_scan_depth")
    scroll_swipes = chat.get("max_scroll_swipes")
    dry_run = chat.get("dry_run")

    env_reply = os.getenv("CHAT_REJECTION_REPLY_TEXT")
    if env_reply and env_reply.strip():
        reply_text = env_reply.strip()
    env_depth = os.getenv("CHAT_MAX_SCAN_DEPTH")
    if env_depth and env_depth.strip():
        scan_depth = env_depth.strip()
    env_swipes = os.getenv("CHAT_MAX_SCROLL_SWIPES")
    if env_swipes and env_swipes.strip():
        scroll_swipes = env_swipes.strip()
    env_dry_run = os.getenv("CHAT_DRY_RUN")
    if env_dry_run and env_dry_run.strip():
        dry_run = env_dry_run.strip()

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
    """Whether a starting service queues a rejection sweep before searching."""
    from .rejection import coerce_bool

    merged = settings if settings is not None else load_settings(config_path=config_path)
    raw: Any = merged.get("run_cleanup_on_startup")

    env_value = os.getenv("RUN_CLEANUP_ON_STARTUP")
    if env_value and env_value.strip():
        raw = env_value.strip()

    return coerce_bool(raw, default=True)


def resolve_enable_scheduler(
    explicit: bool | None = None,
    config_path: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> bool:
    """Whether the integrated Cron scheduler daemon is enabled."""
    from .rejection import coerce_bool

    if explicit is not None:
        return bool(explicit)

    merged = settings if settings is not None else load_settings(config_path=config_path)
    raw: Any = merged.get("enable_scheduler")

    env_value = os.getenv("ENABLE_SCHEDULER")
    if env_value and env_value.strip():
        raw = env_value.strip()

    return coerce_bool(raw, default=True)


# --------------------------------------------------------------------------- #
# Screening policy loading and writable-store resolution
# --------------------------------------------------------------------------- #


def load_screening_policy(config_path: str | Path | None = None):
    """Load the Screening Policy from the one merged Configuration Realm view.

    ``read_config`` projects this same view, so the dashboard and the worker cannot
    disagree about the active policy. Only ``source_path`` needs its own lookup: it
    answers "which file did the operator state this in", which the merged values cannot.
    """
    from .screening_policy import ScreeningPolicy

    if config_path:
        target = Path(config_path)
        if target.is_file():
            data = _parse_file(target) or {}
            policy = ScreeningPolicy.from_dict(data)
            policy.source_path = str(target)
            return policy
        return ScreeningPolicy()

    policy = ScreeningPolicy.from_dict(load_settings())
    source = _screening_source_file()
    if source is not None:
        policy.source_path = str(source)
    return policy


def is_writable_screening_path(path: str | Path) -> bool:
    """Whether ``path`` may be written to: YAML, and never a tracked example."""
    candidate = Path(path)
    return candidate.suffix in (".yaml", ".yml") and ".example." not in candidate.name


def resolve_writable_screening_config_path(root: str | Path | None = None) -> Path:
    """The unified local settings file: the only screening store that takes effect."""
    base = Path(root) if root is not None else resolve_git_common_root()
    for name in ("settings.local.yaml", "settings.local.yml"):
        candidate = base / "config" / name
        if candidate.is_file():
            return candidate
    return base / "config" / "settings.local.yaml"


def persist_company_blacklist(policy: Any, company_name: str) -> Path | None:
    """Write one blacklisted company back to the policy's active config file."""
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


# --------------------------------------------------------------------------- #
# Typed accessors
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PocketBaseSettings:
    """Where the State Stream Broker lives and keeps its SQLite state."""

    url: str
    data_dir: str
    db_path: str


@dataclass(frozen=True)
class LlmSettings:
    """The chat-completion client's connection and ceilings."""

    #: The wire protocol to speak — ``openai`` (Chat Completions) or ``anthropic``
    #: (Messages). This realm only carries the value; the protocol a client is built
    #: from is resolved by :func:`boss_agent.llm_config.create_llm_client`, which falls
    #: back to the default protocol for any other value. A vendor is chosen by
    #: ``base_url`` and ``model``, never by this field.
    provider: str
    base_url: str
    api_key: str | None
    model: str
    temperature: float
    timeout_sec: float
    max_tokens: int
    langsmith_tracing: bool
    langsmith_api_key: str | None
    langsmith_project: str

    @property
    def has_usable_api_key(self) -> bool:
        """False when unset or still a masked/template display value."""
        if not self.api_key or self.api_key == PLACEHOLDER_API_KEY:
            return False
        return not is_mask_placeholder(self.api_key)


@dataclass(frozen=True)
class WorkerSettings:
    """What the Automation Worker needs to find its device and reach its services."""

    device: str
    avd_name: str
    server_url: str
    pocketbase_url: str
    run_cleanup_on_startup: bool


def _section(settings: dict[str, Any] | None, config_path: str | Path | None) -> dict[str, Any]:
    return settings if settings is not None else load_settings(config_path=config_path)


def _as_number(value: Any, fallback: float, *, integer: bool = False) -> Any:
    """Coerce a configured number, falling back rather than raising mid-task.

    A malformed value in a hand-edited YAML file should not turn every task into a
    crash; the schema-less file format makes that a real possibility.
    """
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid numeric setting %r; using %s", value, fallback)
        return int(fallback) if integer else float(fallback)


def pocketbase_settings(
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> PocketBaseSettings:
    merged = _section(settings, config_path)
    return PocketBaseSettings(
        url=merged.get("pocketbase_url") or DEFAULT_POCKETBASE_URL,
        data_dir=merged.get("pocketbase_data_dir") or DEFAULT_POCKETBASE_DATA_DIR,
        db_path=merged.get("pocketbase_db_path") or DEFAULT_POCKETBASE_DB_PATH,
    )


def llm_settings(
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> LlmSettings:
    merged = _section(settings, config_path)
    return LlmSettings(
        provider=normalize_provider(merged.get("provider")),
        base_url=merged.get("base_url") or DEFAULTS["base_url"],
        api_key=merged.get("api_key"),
        model=merged.get("model") or DEFAULTS["model"],
        temperature=_as_number(merged.get("temperature"), DEFAULTS["temperature"]),
        timeout_sec=_as_number(merged.get("timeout_sec"), DEFAULTS["timeout_sec"]),
        max_tokens=_as_number(merged.get("max_tokens"), DEFAULTS["max_tokens"], integer=True),
        langsmith_tracing=bool(merged.get("langsmith_tracing", DEFAULTS["langsmith_tracing"])),
        langsmith_api_key=merged.get("langsmith_api_key"),
        langsmith_project=merged.get("langsmith_project") or DEFAULTS["langsmith_project"],
    )


def worker_settings(
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> WorkerSettings:
    from .rejection import coerce_bool

    merged = _section(settings, config_path)
    return WorkerSettings(
        device=merged.get("device") or DEFAULTS["device"],
        avd_name=merged.get("avd_name") or DEFAULTS["avd_name"],
        server_url=merged.get("server_url") or DEFAULT_SERVER_URL,
        pocketbase_url=merged.get("pocketbase_url") or DEFAULT_POCKETBASE_URL,
        run_cleanup_on_startup=coerce_bool(merged.get("run_cleanup_on_startup"), default=True),
    )


#: Resolved-value printer for the runner scripts. `key=value` lines on stdout, one per
#: requested key, so shell scripts stop mining YAML with `grep|awk|tr` pipelines that
#: silently break when the file layout changes.
def format_resolved_values(keys: list[str], settings: dict[str, Any] | None = None) -> str:
    merged = _section(settings, None)
    lines: list[str] = []
    for key in keys:
        value = merged.get(key)
        if value is None:
            value = ""
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"{key}={value}")
    return "\n".join(lines)


__all__ = [
    "CHAT_DEFAULTS",
    "CONFIG_CHAIN",
    "DEFAULTS",
    "DEFAULT_LLM_PROTOCOL",
    "ENV_OVERRIDES",
    "KEY_ALIASES",
    "LEGACY_LLM_FILE",
    "LLM_PROTOCOLS",
    "LlmSettings",
    "MASK_MARKERS",
    "PLACEHOLDER_API_KEY",
    "PLACEHOLDER_SECRETS",
    "PocketBaseSettings",
    "SECRET_KEYS",
    "WorkerSettings",
    "append_company_blacklist_entry",
    "format_resolved_values",
    "invalidate_cache",
    "is_mask_placeholder",
    "is_writable_screening_path",
    "llm_settings",
    "load_chain",
    "load_screening_policy",
    "load_settings",
    "normalize_provider",
    "normalize_url",
    "patch_settings",
    "persist_company_blacklist",
    "pocketbase_settings",
    "read_config",
    "resolve_chain",
    "resolve_chat_acknowledgment_settings",
    "resolve_communication_cooldown_days",
    "resolve_enable_scheduler",
    "resolve_git_common_root",
    "resolve_pocketbase_data_dir",
    "resolve_pocketbase_db_path",
    "resolve_pocketbase_url",
    "resolve_run_cleanup_on_startup",
    "resolve_server_url",
    "resolve_writable_screening_config_path",
    "resolve_writable_settings_path",
    "save_screening_policy",
    "worker_settings",
]
