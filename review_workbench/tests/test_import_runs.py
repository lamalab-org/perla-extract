from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from review_workbench.import_runs import _read, _source_path, import_run
from review_workbench.import_vercel_runs import _refresh_run
from review_workbench.study_review import StudyReviewStore


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


def test_optional_missing_source_reads_as_absent():
    assert _read(None, required=False) is None


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


def test_refresh_run_promotes_extraction_and_matching_document_together(
    tmp_path, empty_study, document_payload
):
    run_dir = tmp_path / "10.0000--example"
    run_dir.mkdir()
    replacement = dict(empty_study)
    replacement["unresolved_notes"] = ["Revised draft"]
    reparsed = dict(document_payload)
    reparsed["blocks"] = [dict(block) for block in document_payload["blocks"]]
    reparsed["blocks"][0]["block_id"] = "reparsed-p1"
    for name, value in {
        "extraction.json": replacement,
        "document.json": reparsed,
        "validation.json": {"status": "verified", "issues": []},
        "run_configuration.json": {"model": "frontier"},
    }.items():
        _write_json(run_dir / name, value)
    store = StudyReviewStore(tmp_path / "review")
    store.import_seed(
        "dev",
        run_dir.name,
        empty_study,
        document=document_payload,
        manifest={},
        reviewer_id="seed-import",
    )
    app = SimpleNamespace(store=store)

    assert _refresh_run(app, run_dir, "dev", "admin", apply=False) is True
    assert store.revision("dev", run_dir.name) == 1
    assert _refresh_run(app, run_dir, "dev", "admin") is True

    assert store.revision("dev", run_dir.name) == 2
    assert store.load_document("dev", run_dir.name) == reparsed
    assert _refresh_run(app, run_dir, "dev", "admin") is False
