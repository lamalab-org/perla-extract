"""Run an isolated reviewer UI with invented evidence and two correctable errors.

No source papers, API keys, or extraction calls are needed. The generated one-page
PDF and review state live in a temporary directory removed when the server stops.
"""

from __future__ import annotations

import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import click
import pymupdf as fitz

# Match the repository-local server entry point even before an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perla_extract.study_extraction.models import (  # noqa: E402
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    PerformanceObservation,
    ReportedValue,
    StudyExtraction,
)
from review_workbench.server import ReviewApplication, make_handler  # noqa: E402

PAPER_ID = "10.0000--synthetic-ui-demo"
SOURCE_TEXT = (
    "Synthetic reviewer training example; not a published experiment. "
    "Control and Alternative are two device families. Both use ITO/perovskite/Ag. "
    "The champion cell belongs to the Alternative family, not Control. "
    "Its reverse-scan PCE is 20%. The preliminary estimate of 21% was discarded."
)


def create_demo(root: Path) -> ReviewApplication:
    """Seed the real application through its import boundary, without model calls."""

    citation = EvidenceCitation(block_id="demo-main-1", quote=SOURCE_TEXT)
    families = [
        DeviceFamily(
            family_id=identifier,
            label=label,
            variant=None,
            architecture=None,
            polarity="not_reported",
            full_stack_raw="ITO/perovskite/Ag",
            layers=[],
            absorbers=[],
            processing_steps=[],
            evidence=[citation],
        )
        for identifier, label in (
            ("control", "Control"),
            ("alternative", "Alternative"),
        )
    ]
    study = StudyExtraction(
        paper=PaperMetadata(
            title="Synthetic UI demo: correct the owner and PCE", doi=None
        ),
        device_families=families,
        individual_devices=[
            IndividualDevice(
                device_id="champion",
                family_id="control",
                label="Champion cell",
                variant=None,
                champion_status="yes",
                selection_basis="champion",
                evidence=[citation],
            )
        ],
        performance_observations=[
            PerformanceObservation(
                observation_id="reverse-scan",
                device_id="champion",
                measurement_type="jv_scan",
                scan_direction="reverse",
                metrics=[
                    ReportedValue(
                        name="PCE",
                        raw_value="21%",
                        value_number=21,
                        unit="%",
                        evidence=[citation],
                    )
                ],
                evidence=[citation],
            )
        ],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    block = EvidenceBlock(
        block_id=citation.block_id,
        source="main",
        page=1,
        section_path=["Invented example"],
        kind="text",
        text=SOURCE_TEXT,
    )
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 545, 450), SOURCE_TEXT, fontsize=14)
        pdf_bytes = pdf.tobytes()
    application = ReviewApplication(root / "pdfs", root / "review-data")
    application.import_paper(
        "dev",
        PAPER_ID,
        pdf_bytes,
        study.model_dump_json().encode(),
        document_bytes=json.dumps({"blocks": [block.model_dump(mode="json")]}).encode(),
        reviewer_id="synthetic-demo",
    )
    return application


@click.command()
@click.option("--port", default=8765, type=click.IntRange(0, 65535), show_default=True)
def main(port: int) -> None:
    """Launch a disposable local review workspace with synthetic source evidence."""

    with TemporaryDirectory(prefix="perla-review-demo-") as directory:
        application = create_demo(Path(directory))
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(application))
        click.echo(f"Synthetic review workspace: http://127.0.0.1:{server.server_port}")
        click.echo(
            "Open Records. Correct Champion cell's linked family to Alternative;"
        )
        click.echo(
            "then correct reverse-scan PCE from 21% to 20%. Ctrl-C stops and removes this demo."
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
