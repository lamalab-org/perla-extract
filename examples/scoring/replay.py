"""Rescore a saved results CSV and explain changes without running extraction.

Input is the existing results table with paper_id and run_directory columns.
Every row remains visible, including unavailable or invalid saved attempts. A
fresh output directory preserves original scores and supports an optional frozen
baseline comparison through the normal scientific decision ledger.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

import click

from examples.scoring.score_changes import decision_change, decisions
from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.evaluation import EvaluationConfig, evaluate_study
from perla_extract.study_extraction.evaluation_cli import _prediction
from perla_extract.study_extraction.reference_io import load_reference
from perla_extract.study_extraction.run_artifacts import (
    file_digest,
    verify_run_manifest,
    verify_source_hashes,
)


def _environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{name: version(name) for name in ("pydantic", "pint")},
    }


def _fingerprint() -> str:
    from perla_extract.study_extraction import evaluation

    files = {
        path.name: file_digest(path)
        for path in sorted(Path(evaluation.__file__).parent.glob("*.py"))
    }
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"paper_id", "run_directory"} <= set(reader.fieldnames or []):
            raise ValueError("results CSV requires paper_id and run_directory columns")
        rows = list(reader)
    if not rows:
        raise ValueError("results CSV contains no saved attempts")
    return rows


def _field_rows(key: str, paper: str, before: dict, after: dict) -> list[dict]:
    rows = []
    for view in ("attributed", "recovery"):
        old = before["core_facts"]
        new = after["core_facts"]
        if view == "recovery":
            old, new = old["recovery"], new["recovery"]
        for field in sorted(old["fields"].keys() | new["fields"].keys()):
            row = {"run": key, "paper_id": paper, "view": view, "field": field}
            for prefix, ledger in (("before", old), ("after", new)):
                score = ledger["fields"].get(field, {})
                row.update(
                    {
                        f"{prefix}_{name}": score.get(name, 0)
                        for name in ("matched", "predicted", "truth")
                    }
                )
            rows.append(row)
    return rows


def _changes(before: dict, after: dict) -> list[dict]:
    changed = []
    for view in ("attributed", "recovery"):
        for side in ("truth", "prediction"):
            old, new = decisions(before, view, side), decisions(after, view, side)
            old_diagnostics = {
                row["path"]: row
                for row in before["core_facts"]["diagnostics"]["facts"][side]
            }
            new_diagnostics = {
                row["path"]: row
                for row in after["core_facts"]["diagnostics"]["facts"][side]
            }
            for path in sorted(old.keys() | new.keys()):
                kind = decision_change(old.get(path), new.get(path))
                if kind != "unchanged":
                    changed.append(
                        {
                            "view": view,
                            "side": side,
                            "path": path,
                            "change": kind,
                            "before": old.get(path),
                            "after": new.get(path),
                            "before_diagnostic": old_diagnostics.get(
                                old.get(path, {}).get("canonical_path")
                            ),
                            "after_diagnostic": new_diagnostics.get(
                                new.get(path, {}).get("canonical_path")
                            ),
                        }
                    )
    return changed


def replay(
    runs: Path, references: Path, output: Path, baseline: Path | None = None
) -> dict:
    """Bind inputs before scoring; a comparison rejects environment or input drift."""
    rows = _rows(runs)
    previous = (
        json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
        if baseline
        else None
    )
    environment = _environment()
    if previous and previous["environment"] != environment:
        raise ValueError(
            "baseline and revised scoring require the same dependency environment"
        )
    if previous and previous["roster_sha256"] != file_digest(runs):
        raise ValueError("saved-attempt roster differs from the baseline")
    config = EvaluationConfig()
    if previous and previous["config"] != config.model_dump(mode="json"):
        raise ValueError("evaluation settings differ from the frozen baseline")
    if previous and len(previous["entries"]) != len(rows):
        raise ValueError("baseline does not account for every roster row")
    policy_fingerprint = _fingerprint()
    output.mkdir(parents=True, exist_ok=False)
    entries, field_rows, changed = [], [], []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        key = f"{index:04d}"
        paper = row["paper_id"]
        entry: dict = {
            "key": key,
            "paper_id": paper,
            "run_directory": row["run_directory"],
        }
        try:
            if not paper or Path(paper).name != paper or paper in {".", ".."}:
                raise ValueError("paper_id must be one reference directory name")
            run = Path(row["run_directory"])
            run = run if run.is_absolute() else runs.parent / run
            reference = references / paper
            seal, seal_digest = verify_run_manifest(run)
            if row.get("run_id") and row["run_id"] != seal.run_id:
                raise ValueError("saved run_id differs from the roster")
            if seal.run_id in seen_ids:
                raise ValueError("the same saved run_id occurs more than once")
            seen_ids.add(seal.run_id)
            truth, provenance = load_reference(reference)
            actual, validation, efficiency = _prediction(run)
            if provenance:
                verify_source_hashes(
                    provenance.source_sha256,
                    json.loads(
                        (run / "run_configuration.json").read_text(encoding="utf-8")
                    ),
                )
            inputs = {
                "prediction": file_digest(run / "extraction.json"),
                "evidence": file_digest(run / "document.json"),
                "reference": file_digest(reference / "ground_truth.json"),
                "reference_manifest": file_digest(reference / "manifest.json"),
                "run_manifest": seal_digest,
            }
            if previous and previous["entries"][index].get("inputs") != inputs:
                raise ValueError(
                    "saved prediction, evidence or reference differs from baseline"
                )
            score = evaluate_study(
                truth,
                actual,
                benchmark=provenance,
                config=config,
                prediction_validation=validation,
                run_efficiency=efficiency,
                run_id=seal.run_id,
                run_manifest_sha256=seal_digest,
            )
            payload = score.model_dump(mode="json")
            target = output / key / "evaluation.json"
            target.parent.mkdir()
            write_json_exclusive(target, payload)
            entry.update(
                status="scored",
                run_id=seal.run_id,
                inputs=inputs,
                evaluation_sha256=file_digest(target),
            )
            if baseline:
                old_path = baseline / key / "evaluation.json"
                if (
                    file_digest(old_path)
                    != previous["entries"][index]["evaluation_sha256"]
                ):
                    raise ValueError("baseline evaluation differs from its saved hash")
                old = json.loads(old_path.read_text(encoding="utf-8"))
                field_rows.extend(_field_rows(key, paper, old, payload))
                changed.extend(
                    {"run": key, "paper_id": paper, **item}
                    for item in _changes(old, payload)
                )
        except (OSError, ValueError, KeyError, click.ClickException) as exc:
            entry.update(status="unavailable", error=str(exc))
        entries.append(entry)
        click.echo(f"{key} {paper}: {entry['status']}")
    if _fingerprint() != policy_fingerprint:
        raise ValueError(
            "scoring source changed during replay; keep this attempt and choose a fresh output"
        )
    manifest = {
        "roster_sha256": file_digest(runs),
        "environment": environment,
        "code_sha256": policy_fingerprint,
        "config": config.model_dump(mode="json"),
        "entries": entries,
        "scored": sum(e["status"] == "scored" for e in entries),
        "unavailable": sum(e["status"] != "scored" for e in entries),
    }
    write_json_exclusive(output / "manifest.json", manifest)
    if baseline:
        write_json_exclusive(output / "decision-changes.json", changed)
        if field_rows:
            with (output / "field-deltas.csv").open(
                "x", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.DictWriter(stream, fieldnames=list(field_rows[0]))
                writer.writeheader()
                writer.writerows(field_rows)
    return manifest


@click.command()
@click.option(
    "--runs",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Existing results CSV with paper_id and run_directory.",
)
@click.option(
    "--references",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option(
    "--baseline", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
def main(runs: Path, references: Path, output: Path, baseline: Path | None) -> None:
    try:
        result = replay(runs, references, output, baseline)
    except (OSError, ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Scored {result['scored']}; unavailable {result['unavailable']}. No inference was run."
    )
    if result["unavailable"]:
        raise click.ClickException(
            "Some attempts could not be scored; inspect manifest.json"
        )


if __name__ == "__main__":
    main()
