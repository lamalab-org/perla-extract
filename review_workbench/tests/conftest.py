from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


@pytest.fixture
def empty_study() -> dict:
    return {
        "paper": {"title": "Example photovoltaic study", "doi": "10.0000/example"},
        "device_families": [],
        "individual_devices": [],
        "performance_observations": [],
        "population_statistics": [],
        "stability_tests": [],
        "unresolved_notes": ["Initial model note"],
    }


@pytest.fixture
def document_payload() -> dict:
    return {
        "blocks": [
            {
                "block_id": "main_p1_text_1",
                "source": "main",
                "page": 1,
                "section_path": ["Results"],
                "kind": "text",
                "text": "The champion device reached a PCE of 24.1%.",
            },
            {
                "block_id": "supplement_p3_table_1",
                "source": "supplement",
                "page": 3,
                "section_path": ["Device statistics"],
                "kind": "table",
                "text": "Twenty devices were included in the distribution.",
            },
        ]
    }
