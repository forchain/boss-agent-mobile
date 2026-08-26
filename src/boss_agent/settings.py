"""
src/boss_agent/settings.py
==========================
Centralized configuration loading and PocketBase State Stream URL resolution.
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_SEARCH_PATHS: list[Path] = [
    Path("config/settings.local.yaml"),
    Path("config/settings.local.json"),
    Path("config/candidate.local.yaml"),
    Path("config/settings.yaml"),
    Path("config/settings.example.yaml"),
]

DEFAULT_POCKETBASE_URL: str = "http://127.0.0.1:8090"


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
        "search_id": "default_agent_search",
        "keyword": None,
        "enable_search": True,
        "enable_filter": True,
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
                loaded = (
                    yaml.safe_load(content)
                    if p.suffix in [".yaml", ".yml"]
                    else json.loads(content)
                )
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if v is not None:
                            merged[k] = v
                    if "pb_url" in loaded and loaded["pb_url"] is not None:
                        merged["pocketbase_url"] = loaded["pb_url"]
            except Exception:
                pass

    env_pb_url = os.getenv("POCKETBASE_URL")
    if env_pb_url and env_pb_url.strip():
        merged["pocketbase_url"] = env_pb_url

    if "pocketbase_url" in merged and isinstance(merged["pocketbase_url"], str):
        merged["pocketbase_url"] = normalize_url(merged["pocketbase_url"])

    return merged

