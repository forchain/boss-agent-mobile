"""
boss_agent.screening_prompt
===========================
Loader for the single living Screening Prompt document (ADR 0010, Spec #340).

The Screening Prompt is the one and only long-term screening memory surface:
a plain Markdown document in the Configuration Realm. Precedence: an existing
``config/screening_prompt.local.md`` is authoritative even when empty; the
``screening_prompt.example.md`` seed only serves as the default before the
first save. Missing documents fail loudly — nothing is ever fabricated.
"""

from pathlib import Path

from .settings import resolve_git_common_root

LOCAL_FILENAME = "screening_prompt.local.md"
SEED_FILENAME = "screening_prompt.example.md"


def candidate_paths(config_path: str | Path | None = None) -> list[Path]:
    """Resolve the load order: explicit path, then the shared-root document
    (local wins over seed), and finally the cwd copy of the *seed* only.

    A stray local document under the cwd must never shadow the shared-root
    document that the Web Settings UI reads and writes.
    """
    if config_path is not None:
        return [Path(config_path)]

    root = resolve_git_common_root()
    cwd = Path.cwd()
    return [
        root / "config" / LOCAL_FILENAME,
        root / "config" / SEED_FILENAME,
        cwd / "config" / SEED_FILENAME,
    ]


def load_screening_prompt(config_path: str | Path | None = None) -> str:
    """Load the settled Screening Prompt text verbatim.

    Raises FileNotFoundError when neither a local document nor the seed
    default can be found, so callers surface a real error instead of
    silently screening without guidance.
    """
    checked = candidate_paths(config_path)
    for path in checked:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "Screening Prompt document not found. Checked: " + ", ".join(str(p) for p in checked)
    )
