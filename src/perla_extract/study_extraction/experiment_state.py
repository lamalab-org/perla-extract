"""Reserve campaign spending before requests and reconcile sealed run reports.

A missing response price never releases a reservation. A reserved slot is never
re-dispatched by resume, even if the process died before writing its first event.
The JSON record is inspectable; an operating-system lock permits one writer.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from pydantic import Field

from .artifacts import write_json_atomic
from .experiment_plan import CampaignBudget
from .models import StrictModel
from .run_artifacts import load_run_efficiency, verify_run_manifest


class BudgetExceeded(ValueError):
    pass


class SpendingEntry(StrictModel):
    reservation_usd: float = Field(ge=0, allow_inf_nan=False)
    known_cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    cost_complete: bool = False
    status: str = "reserved"
    run_id: str | None = None
    manifest_sha256: str | None = None


class SpendingRecord(StrictModel):
    plan_sha256: str
    attempts: dict[str, SpendingEntry] = Field(default_factory=dict)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


@contextmanager
def campaign_lock(output: Path):
    """Release the single-writer lock automatically, including on process death."""
    with (output / ".experiment.lock").open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt

            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("Another process is running this experiment") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Another process is running this experiment") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class CampaignSpending:
    def __init__(self, output: Path, plan_sha256: str, budget: CampaignBudget | None):
        self.path = output / "spending.json"
        self.budget = budget
        self.record = (
            SpendingRecord.model_validate_json(self.path.read_bytes())
            if self.path.exists()
            else SpendingRecord(plan_sha256=plan_sha256)
        )
        if self.record.plan_sha256 != plan_sha256:
            raise ValueError("Spending record belongs to a different experiment plan")

    def save(self) -> None:
        """Persist a reservation before provider work, not merely in process memory."""
        write_json_atomic(self.path, self.record.model_dump(mode="json"))
        # Windows flushing requires a write-capable handle.
        with self.path.open("r+b") as stream:
            os.fsync(stream.fileno())
        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def committed(self) -> Decimal:
        carry = (
            _decimal(self.budget.prior_known_cost_usd)
            + _decimal(self.budget.prior_unknown_reservation_usd)
            if self.budget
            else Decimal(0)
        )
        return carry + sum(
            (
                _decimal(e.known_cost_usd)
                if e.cost_complete
                else max(_decimal(e.reservation_usd), _decimal(e.known_cost_usd))
                for e in self.record.attempts.values()
            ),
            Decimal(0),
        )

    def reserve(self, key: str, reservation: float | None) -> None:
        if key in self.record.attempts:
            raise ValueError(
                f"Attempt already reserved; resume must not dispatch it: {key}"
            )
        hold = reservation or 0
        if self.budget and self.committed() + _decimal(hold) > _decimal(
            self.budget.max_cost_usd
        ):
            raise BudgetExceeded(
                f"Campaign admission stopped: ${self.committed()} committed; ${hold} required; cap ${self.budget.max_cost_usd}"
            )
        self.record.attempts[key] = SpendingEntry(reservation_usd=hold)
        self.save()

    def reconcile(self, key: str, directory: Path) -> None:
        """Only a verified terminal report can replace its original reservation."""
        manifest, digest = verify_run_manifest(directory, accounting_only=True)
        usage = load_run_efficiency(directory / "report.json")
        entry = self.record.attempts[key]
        if entry.manifest_sha256 and entry.manifest_sha256 != digest:
            raise ValueError(f"Sealed accounting changed for {key}")
        entry.known_cost_usd = usage.cost_usd
        entry.cost_complete = usage.cost_tracking_complete is True
        entry.status = usage.status
        entry.run_id = manifest.run_id
        entry.manifest_sha256 = digest
        self.save()

    def summary(self) -> dict:
        entries = self.record.attempts.values()
        return {
            "known_run_cost_usd": sum(e.known_cost_usd for e in entries),
            "unresolved_attempts": sum(not e.cost_complete for e in entries),
            "committed_usd": float(self.committed()),
            "cap_usd": self.budget.max_cost_usd if self.budget else None,
            "note": "Admission reservations are estimates, not provider price guarantees. Unknown costs retain the larger of the reservation and observed cost.",
        }
