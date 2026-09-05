"""
src/boss_agent/settings.py
==========================
Centralized configuration loading and PocketBase URL/database path resolution.
"""

import json
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

DEFAULT_CONFIG_SEARCH_PATHS: list[Path] = [
    Path("config/settings.local.yaml"),
    Path("config/settings.local.json"),
    Path("config/candidate.local.yaml"),
    Path("config/settings.yaml"),
    Path("config/settings.example.yaml"),
]

DEFAULT_POCKETBASE_URL: str = "http://127.0.0.1:8090"
DEFAULT_POCKETBASE_DATA_DIR: str = ".boss_agent/pb_data"
DEFAULT_POCKETBASE_DB_PATH: str = ".boss_agent/pb_data/data.db"


def normalize_url(url: str) -> str:
    """Normalize URL by stripping surrounding whitespace and trailing slashes."""
    return url.strip().rstrip("/")


def resolve_pocketbase_url(
    explicit_url: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    """Resolve PocketBase State Stream Broker URL according to precedence hierarchy:

    1. Explicit programmatic/CLI argument (`explicit_url`)
    2. `POCKETBASE_URL` environment variable
    3. Configuration files (`pocketbase_url` or `pb_url` key)
    4. Fallback default (`http://127.0.0.1:8090`)
    """
    if explicit_url and explicit_url.strip():
        return normalize_url(explicit_url)

    env_url = os.getenv("POCKETBASE_URL")
    if env_url and env_url.strip():
        return normalize_url(env_url)

    settings = load_settings(config_path=config_path)
    return settings.get("pocketbase_url", DEFAULT_POCKETBASE_URL)


def resolve_git_common_root(cwd: str | Path | None = None) -> Path:
    """Resolve the canonical root directory of the repository, accounting for Git worktrees.

    If inside a Git worktree, this returns the root directory containing the common .git directory.
    If outside a Git repository, it falls back to the current working directory.
    """
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
    """Resolve PocketBase data directory according to precedence hierarchy:

    1. Explicit programmatic/CLI argument (`explicit_dir`)
    2. `PB_DATA_DIR` or `POCKETBASE_DATA_DIR` environment variable
    3. Configuration files (`pocketbase_data_dir` or `pb_data_dir`)
    4. Configuration files (`pocketbase_db_path` or `pb_db_path` parent directory)
    5. Fallback default (`.boss_agent/pb_data`)
    """
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


def load_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load merged settings from configuration files with lowest to highest priority."""
    search_paths: list[Path] = []
    if config_path:
        search_paths.append(Path(config_path))
    else:
        search_paths.extend(DEFAULT_CONFIG_SEARCH_PATHS)

    merged: dict[str, Any] = {
        "device": "emulator-5554",
        "avd_name": "boss_avd_arm64",
        "server_url": "http://127.0.0.1:4723",
        "pocketbase_url": DEFAULT_POCKETBASE_URL,
        "pocketbase_data_dir": None,
        "pocketbase_db_path": None,
        "resume_path": None,
        "force_refresh_memory": False,
        "preview_timeout_sec": 3.0,
        "enable_greeting": True,
    }

    # Load from lowest to highest priority so higher priority files overwrite
    for p in reversed(search_paths):
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                if p.suffix in [".yaml", ".yml"]:
                    if yaml is not None:
                        loaded = yaml.safe_load(content)
                    else:
                        loaded = {}
                        for line in content.splitlines():
                            s = line.strip()
                            if s and not s.startswith("#") and ":" in s:
                                k, v = s.split(":", 1)
                                v = v.strip()
                                if " #" in v:
                                    v = v.split(" #", 1)[0].strip()
                                elif "\t#" in v:
                                    v = v.split("\t#", 1)[0].strip()
                                loaded[k.strip()] = v.strip('"').strip("'")
                else:
                    loaded = json.loads(content)
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if v is not None:
                            merged[k] = v
                    if "pb_url" in loaded and loaded["pb_url"] is not None:
                        merged["pocketbase_url"] = loaded["pb_url"]
                    if "pb_data_dir" in loaded and loaded["pb_data_dir"] is not None:
                        merged["pocketbase_data_dir"] = loaded["pb_data_dir"]
                    if "pb_db_path" in loaded and loaded["pb_db_path"] is not None:
                        merged["pocketbase_db_path"] = loaded["pb_db_path"]
            except Exception:
                pass

    env_pb_url = os.getenv("POCKETBASE_URL")
    if env_pb_url and env_pb_url.strip():
        merged["pocketbase_url"] = env_pb_url

    env_pb_data_dir = os.getenv("PB_DATA_DIR") or os.getenv("POCKETBASE_DATA_DIR")
    if env_pb_data_dir and env_pb_data_dir.strip():
        merged["pocketbase_data_dir"] = env_pb_data_dir.strip()

    env_pb_db_path = os.getenv("PB_DB_PATH") or os.getenv("POCKETBASE_DB_PATH")
    if env_pb_db_path and env_pb_db_path.strip():
        merged["pocketbase_db_path"] = env_pb_db_path.strip()

    # Reciprocally derive db_path / data_dir if only one was specified
    if merged["pocketbase_data_dir"] and not merged["pocketbase_db_path"]:
        merged["pocketbase_db_path"] = str(Path(merged["pocketbase_data_dir"]) / "data.db")
    elif merged["pocketbase_db_path"] and not merged["pocketbase_data_dir"]:
        merged["pocketbase_data_dir"] = str(Path(merged["pocketbase_db_path"]).parent)
    elif not merged["pocketbase_data_dir"] and not merged["pocketbase_db_path"]:
        merged["pocketbase_data_dir"] = DEFAULT_POCKETBASE_DATA_DIR
        merged["pocketbase_db_path"] = DEFAULT_POCKETBASE_DB_PATH

    if "pocketbase_url" in merged and isinstance(merged["pocketbase_url"], str):
        merged["pocketbase_url"] = normalize_url(merged["pocketbase_url"])

    return merged
