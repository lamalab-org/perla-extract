#!/usr/bin/env python3
"""Sync selected reviewed ground truths to Blob without losing collaboration state."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from review_workbench.api.index import BlobStore, VercelReviewApplication
from review_workbench.review_evidence import load_evidence, save_evidence


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "src" / "perla_extract" / "data" / "ground_truth"


def sync(papers: list[str], *, metadata: bool, dry_run: bool) -> int:
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")
    with TemporaryDirectory(prefix="perla-ground-truth-sync-") as workspace:
        app = VercelReviewApplication(BlobStore(), Path(workspace))
        changed = 0
        for specification in papers:
            split, paper_id = specification.split(":", 1)
            source = SOURCE / split / f"{paper_id}.json"
            target = app.ground_truth_dir / split / source.name
            if source.read_bytes() == target.read_bytes():
                continue
            changed += 1
            if dry_run:
                continue
            target.write_bytes(source.read_bytes())
            truth = app.load_ground_truth(split, paper_id)
            evidence = load_evidence(app.ground_truth_dir, split, paper_id, truth)
            save_evidence(app.ground_truth_dir, split, paper_id, truth, evidence)
        if metadata:
            source = SOURCE / "review_metadata.json"
            target = app.ground_truth_dir / source.name
            if source.read_bytes() != target.read_bytes():
                changed += 1
                if not dry_run:
                    target.write_bytes(source.read_bytes())
        if changed and not dry_run:
            app._sync_state()
        return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", action="append", default=[], metavar="SPLIT:ID")
    parser.add_argument("--metadata", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"changed={sync(args.paper, metadata=args.metadata, dry_run=args.dry_run)}")


if __name__ == "__main__":
    main()
