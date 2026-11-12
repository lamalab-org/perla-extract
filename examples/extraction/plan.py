"""Plan two extraction runs from an invented paper without making model calls.

The first configuration fits its declared input/context allowance. The second
reserves too little space for the same scientific request. Both save ordinary run
artifacts so a maintainer can trace a limit from configuration to reported outcome.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import click
import pymupdf as fitz

from perla_extract.study_extraction.configuration import ExtractionConfig
from perla_extract.study_extraction.deployment import DeploymentSettings
from perla_extract.study_extraction.workflow import run_extraction

TEXT = (
    "Synthetic request-planning example, not a published experiment. "
    "The control cell uses ITO/perovskite/Ag and has reverse-scan efficiency of 20%."
)


def run(output: Path) -> dict[str, str]:
    """Save a source PDF and two immutable plans; refuse to overwrite prior work."""
    output.mkdir(parents=True, exist_ok=False)
    pdf_path = output / "synthetic.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 545, 450), TEXT, fontsize=14)
        pdf.save(pdf_path)
    config = ExtractionConfig(
        pdf=pdf_path,
        output_dir=output / "fits",
        parser="pymupdf",
        use_claim_ledger=False,
        use_refinement=False,
        use_targeted_repair=False,
        use_enrichment=False,
        max_output_tokens=1024,
        assembly_max_input_tokens=100_000,
        deployments={"assemble": DeploymentSettings(context_tokens=128_000)},
        document_cache_dir=output / "document-cache",
        model_cache_dir=output / "unused-model-cache",
        heartbeat_seconds=0,
        dry_run=True,
    )
    fits = run_extraction(config)
    blocked = run_extraction(
        replace(config, output_dir=output / "blocked", assembly_max_input_tokens=1)
    )
    statuses = {"fits": str(fits["status"]), "blocked": str(blocked["status"])}
    if statuses != {"fits": "dry_run", "blocked": "failed"}:
        raise RuntimeError(f"unexpected planning outcomes: {statuses}")
    return statuses


@click.command()
@click.option("--output", required=True, type=click.Path(path_type=Path))
def main(output: Path) -> None:
    """Create inspectable request plans with no credentials or provider requests."""
    try:
        statuses = run(output)
    except FileExistsError as exc:
        raise click.ClickException(
            f"Output already exists: {output}; choose a new directory."
        ) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(statuses, indent=2))
    click.echo(
        f"Inspect {output / 'fits/report.json'} and {output / 'blocked/report.json'}"
    )


if __name__ == "__main__":
    main()
