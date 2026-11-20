"""Shared prompt-loading tolerance helpers across adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


def load_prompt_or_default(loader: Callable[[], str], default: _T = "") -> str | _T:  # type: ignore[assignment]
    """Load a prompt document, degrading a missing or unreadable one to ``default``.

    Catches ``OSError`` (including ``FileNotFoundError`` and ``PermissionError``) so a
    missing or unreadable seed never aborts a worker run, verification pass, or
    refinement script; callers choose their own fallback (``""`` or ``None``).
    """
    try:
        return loader()
    except OSError:
        return default


__all__ = ["load_prompt_or_default"]
