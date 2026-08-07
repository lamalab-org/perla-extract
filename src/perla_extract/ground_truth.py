"""Ground-truth review metadata and benchmark eligibility helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable


ARTICLE_TYPES_EXCLUDED = {"review", "perspective", "news_and_views"}
TANDEM_SCOPES_EXCLUDED = {"contains_tandem_devices", "tandem_only"}


def review_metadata_path(truth_dir: Path) -> Path:
    """Locate the shared metadata file from a dev/test ground-truth directory."""
    truth_dir = Path(truth_dir)
    candidates = [truth_dir / "review_metadata.json", truth_dir.parent / "review_metadata.json"]
    return next((path for path in candidates if path.exists()), candidates[-1])


def load_review_metadata(truth_dir: Path) -> Dict[str, Any]:
    path = review_metadata_path(truth_dir)
    if not path.exists():
        return {"schema_version": 1, "papers": {}}
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data.get("papers"), dict):
        raise ValueError(f"Invalid review metadata in {path}: 'papers' must be an object")
    return data


def default_paper_metadata() -> Dict[str, Any]:
    return {
        "article_type": "unknown",
        "tandem_scope": "unknown",
        "review_status": "pending",
        "notes": "",
    }


def paper_metadata(metadata: Dict[str, Any], paper_id: str) -> Dict[str, Any]:
    result = default_paper_metadata()
    result.update(metadata.get("papers", {}).get(paper_id, {}))
    return result


def exclusion_reasons(metadata: Dict[str, Any]) -> list[str]:
    reasons = []
    if metadata.get("article_type") in ARTICLE_TYPES_EXCLUDED:
        reasons.append(f"article_type:{metadata['article_type']}")
    if metadata.get("tandem_scope") in TANDEM_SCOPES_EXCLUDED:
        reasons.append(f"tandem_scope:{metadata['tandem_scope']}")
    return reasons


def is_evaluation_eligible(metadata: Dict[str, Any]) -> bool:
    return not exclusion_reasons(metadata)


def eligible_ground_truth_files(
    truth_dir: Path, files: Iterable[Path] | None = None
) -> tuple[list[Path], list[tuple[Path, list[str]]]]:
    """Partition paper JSONs into included and metadata-excluded files."""
    truth_dir = Path(truth_dir)
    manifest = load_review_metadata(truth_dir)
    files = list(files) if files is not None else sorted(truth_dir.glob("*.json"))
    included: list[Path] = []
    excluded: list[tuple[Path, list[str]]] = []
    for path in files:
        reasons = exclusion_reasons(paper_metadata(manifest, path.stem))
        if reasons:
            excluded.append((path, reasons))
        else:
            included.append(path)
    return included, excluded
