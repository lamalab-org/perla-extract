import json
from pathlib import Path

from perla_extract.study_extraction.models import (
    STUDY_SCHEMA_VERSION,
    study_schema_sha256,
)
from perla_extract.study_extraction.workflow import (
    ExtractionConfig,
    prompt_sha256,
    run_extraction,
)

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


def test_dry_run_writes_a_complete_request_plan(tmp_path):
    report = run_extraction(
        ExtractionConfig(
            pdf=FIXTURE,
            supplement=None,
            output_dir=tmp_path / "output",
            parser="pymupdf",
            document_cache_dir=tmp_path / "documents",
            model_cache_dir=tmp_path / "models",
            dry_run=True,
        )
    )

    assert report["status"] == "dry_run"
    assert report["evidence_blocks"] > 0
    assert report["planned_calls"] >= 1
    assert (tmp_path / "output" / "document.json").exists()
    assert (tmp_path / "output" / "extraction.schema.json").exists()
    assert (tmp_path / "output" / "report.json").exists()
    configuration = json.loads(
        (tmp_path / "output" / "run_configuration.json").read_text()
    )
    assert configuration["schema_version"] == STUDY_SCHEMA_VERSION
    assert configuration["schema_sha256"] == study_schema_sha256()
    assert configuration["prompt_sha256"] == prompt_sha256()
    assert all(
        len(configuration[field]) == 64
        for field in ("schema_sha256", "prompt_sha256", "configuration_sha256")
    )
