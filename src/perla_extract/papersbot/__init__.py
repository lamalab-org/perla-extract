"""Discover papers from complementary sources and retrieve available PDFs."""

from .bot import run_papersbot
from .models import (
    BotResult,
    BotRunConfiguration,
    BotState,
    DiscoveryFailure,
    OpenAlexPolicy,
    OpenAlexRunStats,
    PaperRecord,
    PaperRunOutcome,
    SelectionPolicy,
    ZoteroRunStats,
)

__all__ = [
    "BotResult",
    "BotRunConfiguration",
    "BotState",
    "DiscoveryFailure",
    "OpenAlexPolicy",
    "OpenAlexRunStats",
    "PaperRecord",
    "PaperRunOutcome",
    "SelectionPolicy",
    "ZoteroRunStats",
    "run_papersbot",
]
