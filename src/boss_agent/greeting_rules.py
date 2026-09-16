"""
boss_agent.greeting_rules
=========================
Domain model and persistent store for candidate tailored greeting style rules and memory.
"""

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class GreetingStyleRule:
    """Condition-action style rule distilled from critique or manually defined."""

    id: str
    condition: str
    instruction: str
    enabled: bool = True
    source_job: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"rule_{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GreetingStyleRule":
        return cls(
            id=str(data.get("id") or ""),
            condition=str(data.get("condition") or ""),
            instruction=str(data.get("instruction") or ""),
            enabled=bool(data.get("enabled", True)),
            source_job=str(data.get("source_job") or ""),
            created_at=str(data.get("created_at") or ""),
        )


def load_greeting_rules(config_path: str | Path | None = None) -> list[GreetingStyleRule]:
    """Load greeting style rules from local config file with fallback hierarchy.

    Hierarchy:
    1. Explicit config_path
    2. config/greeting_rules.local.yaml
    3. config/greeting_rules.local.yml
    4. config/greeting_rules.local.json
    5. config/greeting_rules.yaml
    6. config/greeting_rules.example.yaml
    """
    paths_to_check: list[Path] = []
    if config_path:
        paths_to_check.append(Path(config_path))
    else:
        try:
            from .settings import resolve_git_common_root

            root = resolve_git_common_root()
        except Exception:
            root = Path.cwd()

        candidate_rel_paths = [
            Path("config/greeting_rules.local.yaml"),
            Path("config/greeting_rules.local.yml"),
            Path("config/greeting_rules.local.json"),
            Path("config/greeting_rules.yaml"),
            Path("config/greeting_rules.example.yaml"),
        ]
        for p in candidate_rel_paths:
            paths_to_check.append(root / p if not p.is_absolute() else p)
            if not p.is_absolute():
                paths_to_check.append(Path.cwd() / p)

    for p in paths_to_check:
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                if p.suffix in (".yaml", ".yml"):
                    try:
                        data = yaml.safe_load(content)
                    except Exception:
                        data = None
                else:
                    import json

                    data = json.loads(content)

                if isinstance(data, dict):
                    raw_rules = data.get("rules") or []
                    if isinstance(raw_rules, list):
                        return [
                            GreetingStyleRule.from_dict(r)
                            for r in raw_rules
                            if isinstance(r, dict)
                        ]
                elif isinstance(data, list):
                    return [
                        GreetingStyleRule.from_dict(r)
                        for r in data
                        if isinstance(r, dict)
                    ]
            except Exception:
                pass

    return []


def save_greeting_rules(
    rules: list[GreetingStyleRule], config_path: str | Path | None = None
) -> Path:
    """Save greeting style rules to declarative local YAML configuration file."""
    if config_path:
        target_path = Path(config_path)
    else:
        try:
            from .settings import resolve_git_common_root

            root = resolve_git_common_root()
        except Exception:
            root = Path.cwd()
        target_path = root / "config" / "greeting_rules.local.yaml"

    target_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# ==============================================================================\n"
        "# Boss Agent Mobile - Greeting Style Rules & Long-term Preferences\n"
        "# Auto-generated & updated by Web UI or manual editing\n"
        "# ==============================================================================\n\n"
    )

    data = {"rules": [r.to_dict() for r in rules]}
    try:
        yaml_str = yaml.dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        target_path.write_text(header + yaml_str, encoding="utf-8")
    except Exception:
        import json

        target_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return target_path
