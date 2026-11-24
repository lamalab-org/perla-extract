"""Verify the reference-bound roster and export inputs for the existing cohort tools.

The committed roster binds main and SI files separately to their original bytes.
The pinned reference release supplies the scientific answers and their hashes.
This command neither parses PDFs nor calls a model; export occurs only after all
references and supplied source files have passed the same checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from perla_extract.study_extraction.reference_dataset import (
    verify_dataset as verify_dataset,
)

DATASET = Path(__file__).resolve().parents[2] / "benchmarks/test/papers.json"


def export_inputs(value: dict, papers: list, output: Path) -> None:
    """Write ordinary parser/cohort inputs without introducing another run format."""
    output.mkdir(parents=True, exist_ok=False)
    cohort = {
        "format_version": 1,
        "name": value["name"],
        "purpose": value["scope"],
        "split": value["role"],
        "max_model_calls_per_paper": 1,
        "papers": [{"paper_id": r["paper_id"]} for r in papers],
    }
    for name, payload in (("papers.json", papers), ("cohort.json", cohort)):
        (output / name).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


@click.command()
@click.option(
    "--dataset",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=DATASET,
)
@click.option(
    "--sources", type=click.Path(path_type=Path, exists=True, file_okay=False)
)
@click.option("--output", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--repeat-subset", is_flag=True, help="Export only the fixed repeatability subset."
)
def main(
    dataset: Path, sources: Path | None, output: Path | None, repeat_subset: bool
) -> None:
    """Check the fixed reference set; optionally export verified local paths."""
    if output is not None and sources is None:
        raise click.UsageError(
            "--output requires --sources so exported file paths are verified"
        )
    try:
        value, papers = verify_dataset(dataset, sources)
        source_count = sum(1 + (r["supplement"] is not None) for r in value["papers"])
        click.echo(
            f"Verified {len(papers)} {value['role']} references and {source_count} source bindings."
        )
        click.echo(
            "PDF bytes: verified by role."
            if sources
            else "PDF bytes: not checked; supply --sources."
        )
        if repeat_subset:
            selected = {r["paper_id"] for r in value["papers"] if r["repeat_check"]}
            papers = [r for r in papers if r["paper_id"] in selected]
            if not papers:
                raise ValueError("The dataset has no selected repeatability papers")
            value = value | {"name": value["name"] + "-repeatability"}
            click.echo(f"Repeatability subset: {len(papers)} papers.")
        if output is not None:
            export_inputs(value, papers, output)
            click.echo(
                f"Exported {len(papers)} mappings to {output}; no inference performed."
            )
        else:
            click.echo("No output files or model requests created.")
    except (OSError, ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
