from __future__ import annotations

import hashlib
import json
from pathlib import Path

from perla_extract.pydantic_model_reduced import PerovskiteSolarCells

GROUND_TRUTH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "perla_extract"
    / "data"
    / "ground_truth"
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_reviewed_historical_ground_truth_matches_its_manifest():
    """Keep completed legacy labels valid and distinguish exclusions from empty data."""

    manifest = json.loads(
        (GROUND_TRUTH / "reviewed_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == "PerovskiteSolarCells"
    assert manifest["schema_kind"] == "historical_reduced"
    for paper in manifest["papers"]:
        payload = json.loads(
            (GROUND_TRUTH / paper["ground_truth_path"]).read_text(encoding="utf-8")
        )
        validated = PerovskiteSolarCells.model_validate(payload)
        assert len(validated.cells) == paper["cell_count"]
        assert _digest(payload) == paper["ground_truth_sha256"]
        if paper["disposition"] == "excluded_non_research":
            assert paper["article_type"] != "research"
            assert not validated.cells
        else:
            assert paper["disposition"] == "included"
            assert paper["article_type"] == "research"
