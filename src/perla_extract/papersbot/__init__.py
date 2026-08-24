"""Discover relevant papers from journal feeds and retrieve open PDFs."""

from .bot import run_papersbot
from .models import BotResult, BotState, PaperRecord, SelectionPolicy

__all__ = [
    "BotResult",
    "BotState",
    "PaperRecord",
    "SelectionPolicy",
    "run_papersbot",
]
