#!/usr/bin/env python3
"""Score every stored model extraction against one or more truth revisions."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from perla_extract.evaluations import calculate_micro_metrics, score_multiple_extractions
from perla_extract.ground_truth import eligible_ground_truth_files
from perla_extract.postprocessing import postprocess


def score_model(extraction_dir: Path, truth_dir: Path) -> dict[str, float | int]:
    pairs = []
    truth_files, excluded = eligible_ground_truth_files(truth_dir)
    with contextlib.redirect_stdout(io.StringIO()):
        for truth_path in truth_files:
            truth = postprocess(json.loads(truth_path.read_text(encoding="utf-8")))
            prediction_path = extraction_dir / truth_path.name
            try:
                prediction = postprocess(
                    json.loads(prediction_path.read_text(encoding="utf-8"))
                )
            except (FileNotFoundError, json.JSONDecodeError):
                prediction = {"cells": []}
            pairs.append((truth, prediction, truth_path.name))
        evaluations, per_key_metrics = score_multiple_extractions(pairs)
    metrics = calculate_micro_metrics(per_key_metrics)
    return {
        "papers": len(truth_files),
        "excluded_papers": len(excluded),
        "truth_devices": sum(item.devices_in_truth for item in evaluations),
        "matched_devices": sum(item.devices_found for item in evaluations),
        "missing_devices": sum(
            max(item.devices_in_truth - item.devices_found, 0)
            for item in evaluations
        ),
        **metrics,
    }


def score_revision(extractions_root: Path, truth_dir: Path) -> dict[str, dict]:
    return {
        model_dir.name: score_model(model_dir, truth_dir)
        for model_dir in sorted(extractions_root.iterdir())
        if model_dir.is_dir() and model_dir.name != "humans"
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("extractions_root", type=Path)
    parser.add_argument(
        "revision",
        nargs="+",
        metavar="NAME=TRUTH_DIR",
        help="Named ground-truth directory, for example current=.../ground_truth/test",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    revisions = {}
    for item in args.revision:
        name, separator, path = item.partition("=")
        if not separator or not name or not path:
            parser.error(f"Invalid revision {item!r}; expected NAME=TRUTH_DIR")
        revisions[name] = score_revision(args.extractions_root, Path(path))
    output = json.dumps({"revisions": revisions}, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
