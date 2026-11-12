"""Local execution events: stable identities, durable outcomes, optional export.

Scientific payloads stay in artifact files. Events reference those files and record
what ran, its parent operation, and whether it completed. A missing terminal event
therefore remains distinguishable from a successful run after interruption.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol

from .logging import logger
from .stage_results import StageOutcome

_stage: ContextVar[tuple[str, str] | None] = ContextVar("perla_stage", default=None)


class EventAdapter(Protocol):
    def handle(self, event: dict[str, object]) -> None: ...


class EventJournal:
    """Append each event locally before forwarding it to optional telemetry.

    Telemetry errors disable that exporter for this execution and cannot change the
    extraction result. Local journal write errors propagate: an unauditable run must
    not silently claim that its accounting was saved.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        run_id: str | None = None,
        adapter: EventAdapter | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / "events.jsonl"
        self.run_id = run_id or uuid.uuid4().hex
        self.adapter = adapter
        self.started_at = time.monotonic()
        self.sequence = 0
        self.stages: list[StageOutcome] = []
        self._lock = threading.RLock()

    @property
    def stage_id(self) -> str | None:
        current = _stage.get()
        return current[1] if current and current[0] == self.run_id else None

    def emit(self, event: str, **fields: object) -> dict[str, object]:
        with self._lock:
            self.sequence += 1
            record = {
                **fields,
                "event_version": 1,
                "event": event,
                "event_id": uuid.uuid4().hex,
                "sequence": self.sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
            }
            if self.stage_id and "stage_id" not in record:
                record["stage_id"] = self.stage_id
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, ensure_ascii=False, allow_nan=False, default=str)
                    + "\n"
                )
                stream.flush()
            if self.adapter is not None:
                try:
                    self.adapter.handle(record)
                except Exception as exc:
                    close = getattr(self.adapter, "close", None)
                    self.adapter = None
                    if close is not None:
                        try:
                            close()
                        except Exception:
                            pass
                    logger.warning(
                        "Trace export disabled; local events remain available ({})",
                        type(exc).__name__,
                    )
                    self.emit(
                        "telemetry.failed",
                        error_type=type(exc).__name__,
                        status="disabled",
                    )
            return record

    @contextmanager
    def stage(self, name: str, **metadata: object) -> Iterator[StageOutcome]:
        stage_id = uuid.uuid4().hex
        parent = self.stage_id
        token = _stage.set((self.run_id, stage_id))
        started = time.monotonic()
        outcome = StageOutcome(stage=name, stage_id=stage_id)
        try:
            self.emit("stage.started", stage=name, parent_stage_id=parent, **metadata)
            with logger.contextualize(
                run_id=self.run_id, stage_id=stage_id, stage=name
            ):
                yield outcome
        except BaseException as exc:
            outcome.record(
                "interrupted"
                if isinstance(exc, (KeyboardInterrupt, SystemExit))
                else "failed",
                errors=[str(exc)],
            )
            outcome.error_type = type(exc).__name__
            raise
        finally:
            outcome.elapsed_seconds = round(time.monotonic() - started, 6)
            self.stages.append(outcome)
            try:
                self.emit("stage.finished", **outcome.to_dict())
            finally:
                _stage.reset(token)


def read_events(path: Path) -> list[dict]:
    """Read complete events, tolerating only an interrupted final partial line."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not line.endswith("\n"):
                break
            raise
    return records
