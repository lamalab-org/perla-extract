#!/usr/bin/env python3
"""Build disposable, evidence-linked dossiers for a careful corpus audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

from review_workbench.review_evidence import (
    fact_suggestions,
    flatten_facts,
    group_quantity_mentions,
    quantity_mentions,
)


def build_dossier(pdf_path: Path, truth_path: Path) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    with fitz.open(pdf_path) as document:
        pages = tuple(page.get_text() for page in document)
    facts = flatten_facts(truth)
    suggestions = fact_suggestions(pages, facts)
    quantities = quantity_mentions(pages, facts)
    unmapped = [item for item in quantities if not item["mapped_paths"]]
    figure_captions = []
    for page_number, page in enumerate(pages, 1):
        for line in page.splitlines():
            stripped = " ".join(line.split())
            if stripped.lower().startswith(("figure ", "fig. ")):
                figure_captions.append({"page": page_number, "text": stripped})
    cells = []
    for index, cell in enumerate(truth.get("cells", [])):
        cells.append(
            {
                "index": index,
                "formula": (cell.get("perovskite_composition") or {}).get("formula"),
                "performance": {
                    key: cell.get(key)
                    for key in ("pce", "jsc", "voc", "ff")
                },
                "aggregation": cell.get("performance_aggregation", cell.get("averaged_quantities")),
                "encapsulated": cell.get("encapsulated"),
                "stability": cell.get("stability"),
                "layers": [
                    {
                        "name": layer.get("name"),
                        "functionality": layer.get("functionality"),
                        "additional_treatment": layer.get("additional_treatment"),
                    }
                    for layer in cell.get("layers") or []
                ],
                "notes": cell.get("additional_notes"),
            }
        )
    return {
        "paper_id": truth_path.stem,
        "split": truth_path.parent.name,
        "pdf_pages": len(pages),
        "cells": cells,
        "fact_evidence": [
            {"path": fact["path"], "value": fact["value"], **suggestions[fact["path"]]}
            for fact in facts
            if fact["path"] in suggestions
        ],
        "unmapped_candidates": group_quantity_mentions(unmapped),
        "figure_captions": figure_captions,
    }


def build_all(pdf_dir: Path, ground_truth_dir: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for split in ("dev", "test"):
        for truth_path in sorted((ground_truth_dir / split).glob("*.json")):
            pdf_path = pdf_dir / f"{truth_path.stem}.pdf"
            if not pdf_path.exists():
                raise FileNotFoundError(pdf_path)
            dossier = build_dossier(pdf_path, truth_path)
            target = output_dir / split / f"{truth_path.stem}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(dossier, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--ground-truth-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_all(args.pdf_dir, args.ground_truth_dir, args.output_dir))


if __name__ == "__main__":
    main()
