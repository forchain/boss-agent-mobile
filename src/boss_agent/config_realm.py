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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

#: Legacy file, consulted only when the chain produced no usable API key. It predates
#: the realm and is kept as an explicit chain entry rather than as a hidden second pass.
LEGACY_LLM_FILE: Path = Path("config/llm.local.yaml")

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


#: The one defaults table. Every Python loader resolves its baseline from here, and
#: `config/defaults.fixture.json` is generated from it so the TypeScript mirror can be
#: asserted against the same values instead of against a comment.
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
    # Screening switches. Declared here so the Python baseline and the Web baseline are
    # the same table; `config/defaults.fixture.json` is generated from it and asserted
    # by both suites.
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
    """Read a compatibility knob from `boss_agent.settings`, late.

    `DEFAULT_CONFIG_SEARCH_PATHS` and `yaml` are rebound by tests and embedders; the
    lookup has to happen at call time or rebinding them would silently stop working.
    """
    from . import settings

    return getattr(settings, name, fallback)


def resolve_config_root() -> Path:
    from .settings import resolve_git_common_root

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
    """The legacy LLM file's stat, as part of the cache key.

    It is resolved *relative to the working directory*, so two loads can share a chain
    and an environment yet read different files. Leaving it out of the key made the
    cache answer with a value read from a different directory entirely.
    """
    return _chain_signature([get_legacy_llm_file()])


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
    for int_key in ("max_tokens", "daily_greeting_limit", "communication_cooldown_days"):
        if int_key in merged and merged[int_key] is not None:
            with contextlib.suppress(ValueError, TypeError):
                merged[int_key] = int(merged[int_key])

    for float_key in ("temperature", "timeout_sec", "preview_timeout_sec"):
        if float_key in merged and merged[float_key] is not None:
            with contextlib.suppress(ValueError, TypeError):
                merged[float_key] = float(merged[float_key])

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
    "PocketBaseSettings",
    "WorkerSettings",
    "format_resolved_values",
    "invalidate_cache",
    "is_mask_placeholder",
    "llm_settings",
    "load_settings",
    "normalize_provider",
    "normalize_url",
    "pocketbase_settings",
    "resolve_chain",
    "worker_settings",
]
