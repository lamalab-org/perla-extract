"""Verify the development roster and export inputs for the existing cohort tools.

The committed roster binds main and SI files separately to their original bytes.
The pinned reference release supplies the scientific answers and their hashes.
This command neither parses PDFs nor calls a model; export occurs only after all
references and supplied source files have passed the same checks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click

from perla_extract.study_extraction.reference_io import (
    load_reference,
    reference_content_digest,
)
from perla_extract.study_extraction.reference_release import ReferenceRelease
from perla_extract.study_extraction.run_artifacts import file_digest

DATASET = Path(__file__).resolve().parents[2] / "benchmarks/development/papers.json"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _check_digest(
    path: Path, expected: str, *, reference_content: bool = False
) -> None:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"Invalid SHA256 for {path}")
    if not path.is_file():
        raise ValueError(f"Missing file: {path}")
    actual = (
        reference_content_digest(_read(path))
        if reference_content
        else file_digest(path)
    )
    if actual != expected:
        raise ValueError(f"SHA256 mismatch: {path}")


def verify_dataset(dataset: Path, sources: Path | None = None) -> tuple[dict, list]:
    """Resolve every reference by paper ID and verify the separately bound sources.

    Without sources this checks the complete public reference bundle, as in CI.
    Supplying sources additionally verifies each article/SI role; swapping those
    files is an error even though their combined set of hashes is unchanged.
    """
    dataset = dataset.resolve()
    value = _read(dataset)
    release_path = (dataset.parent / value["reference_release"]).resolve()
    _check_digest(release_path, value["reference_release_sha256"])
    raw_release = _read(release_path)
    release = ReferenceRelease.model_validate(raw_release)
    if release.split != "dev":
        raise ValueError("The development dataset requires development references")
    entries = {r["paper_id"]: r for r in raw_release["papers"]}
    ids = [r["paper_id"] for r in value["papers"]]
    if len(set(ids)) != len(ids) or set(ids) != set(entries):
        raise ValueError("Dataset roster must match the complete reference release")
    provenance = []
    papers = []
    seen_sources: set[str] = set()
    for row in value["papers"]:
        paper_id = row["paper_id"]
        if row["doi"] != paper_id.replace("--", "/", 1):
            raise ValueError(f"DOI does not match paper ID: {paper_id}")
        entry = entries[paper_id]
        reference = (release_path.parent / entry["path"]).resolve()
        if not reference.is_relative_to(release_path.parent):
            raise ValueError(f"Reference is outside its release: {paper_id}")
        _check_digest(
            reference / "ground_truth.json",
            entry["ground_truth_sha256"],
            reference_content=True,
        )
        _check_digest(
            reference / "manifest.json",
            entry["manifest_sha256"],
            reference_content=True,
        )
        _, benchmark = load_reference(reference)
        if benchmark is None or benchmark.paper_id != paper_id:
            raise ValueError(f"Reference paper ID mismatch: {paper_id}")
        manifest = _read(reference / "manifest.json")
        if "adjudication" not in manifest["review"]["completed_stage_names"]:
            raise ValueError(f"Reference has no declared adjudication: {paper_id}")
        bindings = {role: row[role] for role in ("pdf", "supplement")}
        bound_hashes = []
        resolved = {"paper_id": paper_id, "reference": str(reference)}
        for role, binding in bindings.items():
            if binding is None:
                if role == "pdf":
                    raise ValueError(f"Missing article binding: {paper_id}")
                continue
            filename = binding["file"]
            if (
                not filename
                or Path(filename).name != filename
                or filename in {".", ".."}
            ):
                raise ValueError(f"Source must be a single filename: {paper_id}")
            bound_hashes.append(binding["sha256"])
            if binding["sha256"] in seen_sources:
                raise ValueError(f"Source reused by another role or paper: {paper_id}")
            seen_sources.add(binding["sha256"])
            if sources is not None:
                path = (sources / filename).resolve()
                _check_digest(path, binding["sha256"])
                resolved[role] = str(path)
        if sorted(bound_hashes) != sorted(benchmark.source_sha256):
            raise ValueError(
                f"Bound source hashes do not match the reference: {paper_id}"
            )
        provenance.append(benchmark)
        papers.append(resolved)
    release.verify(provenance)
    return value, papers


def export_inputs(value: dict, papers: list, output: Path) -> None:
    """Write ordinary parser/cohort inputs without introducing another run format."""
    output.mkdir(parents=True, exist_ok=False)
    cohort = {
        "format_version": 1,
        "name": value["name"],
        "purpose": value["scope"],
        "split": "dev",
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
    """Check the fixed development set; optionally export verified local paths."""
    if output is not None and sources is None:
        raise click.UsageError(
            "--output requires --sources so exported file paths are verified"
        )
    try:
        value, papers = verify_dataset(dataset, sources)
        source_count = sum(1 + (r["supplement"] is not None) for r in value["papers"])
        click.echo(
            f"Verified {len(papers)} development references and {source_count} source bindings."
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
