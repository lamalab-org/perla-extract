"""Discover papers from complementary sources and retrieve available PDFs."""

from .acquisition import AcquiredPdf, OpenAccessPdfSource, PdfSource, ZoteroPdfSource
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
    PdfAcquisitionFailure,
    SelectionPolicy,
    ZoteroRunStats,
)

__all__ = [
    "AcquiredPdf",
    "BotResult",
    "BotRunConfiguration",
    "BotState",
    "DiscoveryFailure",
    "OpenAlexPolicy",
    "OpenAlexRunStats",
    "OpenAccessPdfSource",
    "PaperRecord",
    "PaperRunOutcome",
    "PdfAcquisitionFailure",
    "PdfSource",
    "SelectionPolicy",
    "ZoteroRunStats",
    "ZoteroPdfSource",
    "run_papersbot",
]
