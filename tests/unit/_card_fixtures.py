"""
tests/unit/_card_fixtures.py
============================
Shared card fixtures for tests that drive the Mobile Job Feed Pipeline.

The pipeline consumes *located* cards: a parsed `JobCardBrief` paired with the opaque
element it was read from. The element is only touched by the feed-boundary geometry
check and the detail-page tap, so a scripted stand-in needs nothing more than a mock
in that slot — which is what `located()` supplies.
"""

from typing import Any
from unittest.mock import MagicMock

from boss_agent.models import JobCardBrief
from boss_agent.pages import LocatedJobCard


def located(card: JobCardBrief, element: Any | None = None) -> LocatedJobCard:
    """Pair a parsed card brief with a stand-in device element."""
    return LocatedJobCard(card=card, element=element if element is not None else MagicMock())
