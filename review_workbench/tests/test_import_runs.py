from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_workbench.import_runs import _source_path, import_run


class RecordingApplication:
    """Capture the immutable seed contract without constructing the HTTP app."""

    def __init__(self) -> None:
        self.imported: dict[str, object] | None = None

    def import_paper(self, split, paper_id, pdf_bytes, extraction_bytes, **kwargs):
        self.imported = {
            "split": split,
            "paper_id": paper_id,
            "pdf_bytes": pdf_bytes,
            "extraction_bytes": extraction_bytes,
            **kwargs,
        }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_source_path_supports_relocated_run_sources(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    relocated = pdf_dir / "journal-download.pdf"
    relocated.write_bytes(b"%PDF-main")

    result = _source_path(
        {"pdf": "/original/machine/journal-download.pdf"},
        "pdf",
        paper_id="paper",
        pdf_dir=pdf_dir,
    )

    assert result == relocated


def test_import_run_uses_claim_coverage_and_configured_sources(tmp_path):
    run_dir = tmp_path / "paper"
    pdf_dir = tmp_path / "pdfs"
    run_dir.mkdir()
    pdf_dir.mkdir()
    main = pdf_dir / "download.pdf"
    supplement = pdf_dir / "support.pdf"
    main.write_bytes(b"%PDF-main")
    supplement.write_bytes(b"%PDF-supplement")
    _write_json(run_dir / "report.json", {"status": "complete"})
    _write_json(run_dir / "validation.json", {"status": "verified", "issues": []})
    _write_json(run_dir / "extraction.json", {"paper": {}})
    _write_json(run_dir / "document.json", {"blocks": []})
    _write_json(
        run_dir / "run_configuration.json",
        {"pdf": str(main), "supplement": str(supplement)},
    )
    _write_json(run_dir / "claim_coverage_audit.json", {"status": "complete"})
    app = RecordingApplication()

    import_run(
        app,
        run_dir=run_dir,
        pdf_dir=pdf_dir,
        split="dev",
        reviewer_id="seed-import",
    )

    assert app.imported is not None
    assert app.imported["split"] == "dev"
    assert app.imported["pdf_bytes"] == b"%PDF-main"
    assert app.imported["supplement_bytes"] == b"%PDF-supplement"
    assert json.loads(app.imported["coverage_bytes"])["status"] == "complete"


def test_import_run_requires_main_source_in_configuration(tmp_path):
    run_dir = tmp_path / "paper"
    pdf_dir = tmp_path / "pdfs"
    run_dir.mkdir()
    pdf_dir.mkdir()
    _write_json(run_dir / "report.json", {"status": "complete"})
    _write_json(run_dir / "validation.json", {"status": "verified", "issues": []})
    _write_json(run_dir / "run_configuration.json", {"pdf": None})

    with pytest.raises(Exception, match="run configuration has no main PDF"):
        import_run(
            RecordingApplication(),
            run_dir=run_dir,
            pdf_dir=pdf_dir,
            split="dev",
            reviewer_id="seed-import",
        )
