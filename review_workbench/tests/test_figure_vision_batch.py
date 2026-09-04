from __future__ import annotations

import json

from click.testing import CliRunner

from review_workbench.figure_images import FigureImageManifest
from review_workbench.figure_vision_batch import main


def test_render_only_batch_checkpoints_successes_and_failures(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    good = runs / "good"
    bad = runs / "bad"
    good.mkdir(parents=True)
    bad.mkdir()
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    for run in (good, bad):
        (run / "document.json").write_text('{"blocks": []}', encoding="utf-8")
    (good / "run_configuration.json").write_text(
        json.dumps({"pdf": str(pdf)}), encoding="utf-8"
    )

    def fake_manifest(*args, **kwargs):
        return FigureImageManifest(
            format_version=1,
            pdf_path=str(pdf),
            pdf_sha256="a" * 64,
            document_sha256="b" * 64,
            docling_version="test",
            dpi=180,
            margin_points=6,
            figures=[],
            captions_without_region=[{"figure_number": "1"}],
        )

    monkeypatch.setattr(
        "review_workbench.figure_vision_batch.build_figure_image_manifest",
        fake_manifest,
    )
    output = tmp_path / "proposals.json"
    result = CliRunner().invoke(
        main,
        [
            "--runs-dir",
            str(runs),
            "--output-dir",
            str(tmp_path / "images"),
            "--proposal-output",
            str(output),
            "--render-only",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "partial"
    assert payload["papers"]["good"]["localized_figures"] == 0
    assert "bad" in payload["failures"]
