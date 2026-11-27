"""Protect transcription shape, input identity and conservative cost admission."""

import pytest
from pydantic import ValidationError

from examples.parsing.figures import digest
from examples.parsing.frontier_tables import (
    PrintedTable,
    Transcription,
    committed_cost,
    message,
)


def test_table_keeps_missing_cells_and_rejects_shifted_rows():
    table = PrintedTable(
        title=None, headers=["Device", "PCE (%)"], rows=[["A", None]], footnotes=[]
    )
    assert table.rows == [["A", None]]
    with pytest.raises(ValidationError):
        PrintedTable(title=None, headers=table.headers, rows=[["A"]], footnotes=[])
    assert Transcription(tables=[], unreadable_regions=[]).tables == []


def test_context_condition_does_not_change_the_supplied_image(tmp_path):
    path = tmp_path / "crop.png"
    path.write_bytes(b"fixed crop bytes")
    case = {
        "id": "figure",
        "image": str(path),
        "image_sha256": digest(path),
        "caption": "Device A.",
    }
    without = message(case, False)
    with_caption = message(case, True)
    assert without[1] == with_caption[1]
    assert "Device A." not in without[0]["text"]
    assert "Device A." in with_caption[0]["text"]
    path.write_bytes(b"changed crop bytes")
    with pytest.raises(ValueError, match="Crop changed"):
        message(case, False)


def test_unknown_cost_retains_the_entire_reservation():
    plan = {"known_paid_carry_usd": 8.63994496, "held_prior_unknown_usd": 17.45}
    results = [
        {
            "usage": {"cost": 0.1, "cost_tracking_complete": True},
            "reservation_usd": 0.24,
        },
        {
            "usage": {"cost": 0, "cost_tracking_complete": False},
            "reservation_usd": 0.24,
        },
    ]
    assert committed_cost(plan, results) == pytest.approx(26.42994496)
