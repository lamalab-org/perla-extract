"""Reusable heartbeat for operations whose libraries may otherwise stay silent."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from .logging import logger


@contextmanager
def heartbeat(operation: str, interval_seconds: float) -> Iterator[float]:
    """Log elapsed time periodically until a blocking operation returns.

    PDF parsers and HTTP clients are both synchronous. Running only the timer in
    a daemon thread keeps their behavior simple while ensuring a long operation
    never looks like a frozen process.
    """

    started = time.monotonic()
    stopped = threading.Event()

    def report() -> None:
        while not stopped.wait(interval_seconds):
            logger.info(
                "{} still running (elapsed {:.0f}s)",
                operation,
                time.monotonic() - started,
            )

    thread = (
        threading.Thread(target=report, daemon=True) if interval_seconds > 0 else None
    )
    if thread:
        thread.start()
    try:
        yield started
    finally:
        stopped.set()
        if thread:
            thread.join(timeout=1)
