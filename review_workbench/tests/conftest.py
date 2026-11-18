from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


@pytest.fixture(scope="session")
def node_runtime():
    """Frontend behavioral checks are required, including on Python-only CI jobs."""

    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "Frontend tests require Node.js >=20; install it before running pytest."
        )
    result = subprocess.run(
        [node, "--version"], check=True, capture_output=True, text=True
    )
    if int(result.stdout.strip().lstrip("v").split(".")[0]) < 20:
        pytest.fail("Frontend tests require Node.js >=20.")
    return node


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
