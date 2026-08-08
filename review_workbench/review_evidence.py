"""Field-level evidence for review-workbench paper annotations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


REVIEW_STATUSES = {
    "pending",
    "verified",
    "incorrect",
    "not_in_paper",
    "needs_followup",
}
VALUE_RELATIONS = {
    "unspecified",
    "exact",
    "approximately",
    "lower_bound",
    "upper_bound",
    "range",
}
AGGREGATIONS = {
    "unspecified",
    "single_measurement",
    "mean",
    "median",
    "champion",
    "stabilized",
    "distribution",
}

QUANTITY_PATTERN = re.compile(
    r"(?<![\w.])"
    r"(?P<value>-?\d+(?:[.,]\d+)?)"
    r"(?:\s*(?:±|\+/-)\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?P<unit>mA\s*(?:cm(?:\s*\^?\s*[−-]?2|[²⁻]2?)|cm-2)|"
    r"mW\s*(?:cm(?:\s*\^?\s*[−-]?2|[²⁻]2?)|cm-2)|"
    r"A\s*(?:cm(?:\s*\^?\s*[−-]?2|[²⁻]2?)|m-2)|"
    r"mg\s*mL[−-]?1|mg\s*L[−-]?1|g\s*L[−-]?1|mol\s*L[−-]?1|mmol\s*L[−-]?1|"
    r"wt\s*%|vol\s*%|%|mV|eV|V|cm(?:\s*\^?\s*2|²)|mm(?:\s*\^?\s*2|²)|"
    r"°\s*C|º\s*C|K|rpm|ms|seconds?|secs?|s|minutes?|mins?|min|hours?|hrs?|h|"
    r"days?|weeks?|months?|years?|nm|µm|μm|mm|M|suns?)(?!\w)",
    re.IGNORECASE,
)


def ground_truth_digest(ground_truth: dict[str, Any]) -> str:
    canonical = json.dumps(
        ground_truth, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def flatten_facts(value: Any, path: str = "") -> list[dict[str, Any]]:
    """Flatten non-null scalar values into stable JSON Pointer-style facts."""
    facts: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            facts.extend(flatten_facts(child, f"{path}/{escaped}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            facts.extend(flatten_facts(child, f"{path}/{index}"))
    elif value is not None:
        facts.append(
            {
                "path": path or "/",
                "value": value,
                "value_type": type(value).__name__,
            }
        )
    return facts


def evidence_path(ground_truth_dir: Path, split: str, paper_id: str) -> Path:
    return Path(ground_truth_dir) / "evidence" / split / f"{paper_id}.json"


def reconcile_evidence(
    paper_id: str,
    ground_truth: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Align an evidence record with the current ground-truth fields."""
    existing_fields = (existing or {}).get("fields", {})
    fields: dict[str, Any] = {}
    for fact in flatten_facts(ground_truth):
        previous = existing_fields.get(fact["path"], {})
        unchanged = previous.get("value") == fact["value"]
        reviews = previous.get("reviews", {}) if unchanged else {}
        # Migrate the first single-review draft format without losing work.
        if unchanged and not reviews and "status" in previous:
            reviews = {
                "reviewer": {
                    "status": previous.get("status", "pending"),
                    "page": previous.get("page"),
                    "quote": previous.get("quote", ""),
                    "notes": previous.get("notes", ""),
                    "value_relation": previous.get(
                        "value_relation", "unspecified"
                    ),
                    "aggregation": previous.get(
                        "aggregation", "unspecified"
                    ),
                }
            }
        fields[fact["path"]] = {
            "value": fact["value"],
            "reviews": reviews,
        }
    return {
        "schema_version": 2,
        "paper_id": paper_id,
        "ground_truth_sha256": ground_truth_digest(ground_truth),
        "fields": fields,
    }


def load_evidence(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    ground_truth: dict[str, Any],
) -> dict[str, Any]:
    path = evidence_path(ground_truth_dir, split, paper_id)
    existing = None
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            existing = json.load(stream)
    return reconcile_evidence(paper_id, ground_truth, existing)


def save_evidence(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    ground_truth: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    current = reconcile_evidence(paper_id, ground_truth, payload)
    for field in current["fields"].values():
        for review in field["reviews"].values():
            if review.get("status", "pending") not in REVIEW_STATUSES:
                raise ValueError(f"Invalid review status: {review.get('status')}")
            if review.get("value_relation", "unspecified") not in VALUE_RELATIONS:
                raise ValueError(
                    f"Invalid value relation: {review.get('value_relation')}"
                )
            if review.get("aggregation", "unspecified") not in AGGREGATIONS:
                raise ValueError(
                    f"Invalid aggregation: {review.get('aggregation')}"
                )
            page = review.get("page")
            if page is not None and (not isinstance(page, int) or page < 1):
                raise ValueError("Evidence pages must be positive integers")
            review["status"] = review.get("status", "pending")
            review["value_relation"] = review.get(
                "value_relation", "unspecified"
            )
            review["aggregation"] = review.get("aggregation", "unspecified")
            review["quote"] = str(review.get("quote", ""))
            review["notes"] = str(review.get("notes", ""))
    path = evidence_path(ground_truth_dir, split, paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return current


def reviewer_entry(field: dict[str, Any], reviewer_id: str) -> dict[str, Any]:
    review = field.get("reviews", {}).get(reviewer_id, {})
    return {
        "status": review.get("status", "pending"),
        "page": review.get("page"),
        "quote": review.get("quote", ""),
        "notes": review.get("notes", ""),
        "value_relation": review.get("value_relation", "unspecified"),
        "aggregation": review.get("aggregation", "unspecified"),
    }


def review_progress(
    evidence: dict[str, Any], reviewer_id: str | None = None
) -> dict[str, int]:
    fields = evidence.get("fields", {}).values()
    counts = {status: 0 for status in REVIEW_STATUSES}
    total = 0
    for field in fields:
        total += 1
        if reviewer_id:
            status = reviewer_entry(field, reviewer_id)["status"]
        else:
            statuses = [
                review.get("status", "pending")
                for review in field.get("reviews", {}).values()
            ]
            status = next((item for item in statuses if item != "pending"), "pending")
        counts[status] += 1
    reviewed = total - counts["pending"]
    return {"total": total, "reviewed": reviewed, **counts}


def disagreement_paths(evidence: dict[str, Any]) -> list[str]:
    """Return fields where reviewers made conflicting terminal decisions."""
    paths = []
    for path, field in evidence.get("fields", {}).items():
        statuses = {
            review.get("status", "pending")
            for review in field.get("reviews", {}).values()
            if review.get("status", "pending") not in {"pending", "needs_followup"}
        }
        if len(statuses) > 1:
            paths.append(path)
    return paths


def _normalized_page_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _search_variants(value: Any) -> list[str]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        variants = {str(value), f"{value:g}"}
        variants.update(item.replace(".", ",") for item in list(variants))
        return sorted((item for item in variants if len(item) >= 1), key=len, reverse=True)
    text = str(value).strip()
    if 2 <= len(text) <= 80:
        return [text]
    return []


def suggestion_for_fact(
    pages: tuple[str, ...], fact: dict[str, Any]
) -> dict[str, Any] | None:
    """Find a conservative first exact-text suggestion for a fact."""
    for variant in _search_variants(fact["value"]):
        pattern = re.compile(
            (r"(?<![\d.])" + re.escape(variant) + r"(?![\d.])")
            if isinstance(fact["value"], (int, float))
            and not isinstance(fact["value"], bool)
            else re.escape(variant),
            re.IGNORECASE,
        )
        for page_number, page in enumerate(pages, 1):
            normalized = _normalized_page_text(page)
            match = pattern.search(normalized)
            if match:
                start = max(0, match.start() - 90)
                end = min(len(normalized), match.end() + 130)
                return {
                    "page": page_number,
                    "query": variant,
                    "snippet": normalized[start:end],
                    "match_start": match.start() - start,
                    "match_end": match.end() - start,
                }
    return None


def fact_suggestions(
    pages: tuple[str, ...], facts: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Suggest PDF evidence for numeric facts and concise identifying strings."""
    suggestions: dict[str, dict[str, Any]] = {}
    cache: dict[tuple[type, Any], dict[str, Any] | None] = {}
    for fact in facts:
        value = fact["value"]
        is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        is_identifier = fact["path"].endswith(
            ("/formula", "/name", "/method", "/device_architecture")
        )
        if not (is_numeric or is_identifier):
            continue
        cache_key = (type(value), value)
        if cache_key not in cache:
            cache[cache_key] = suggestion_for_fact(pages, fact)
        if cache[cache_key]:
            suggestions[fact["path"]] = cache[cache_key]
    return suggestions


def _numbers_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-5, abs_tol=max(1e-4, abs(right) * 1e-5))


def quantity_mentions(
    pages: tuple[str, ...], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find unit-bearing PDF quantities and map exact values to JSON facts."""
    numeric_facts = [
        fact
        for fact in facts
        if isinstance(fact["value"], (int, float))
        and not isinstance(fact["value"], bool)
    ]
    mentions: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str, int]] = set()
    for page_number, page in enumerate(pages, 1):
        normalized = _normalized_page_text(page)
        for match in QUANTITY_PATTERN.finditer(normalized):
            raw_value = match.group("value")
            try:
                value = float(raw_value.replace(",", "."))
            except ValueError:
                continue
            unit = re.sub(r"\s+", " ", match.group("unit")).strip()
            key = (page_number, raw_value, unit.lower(), match.start())
            if key in seen:
                continue
            seen.add(key)
            mapped_paths = [
                fact["path"]
                for fact in numeric_facts
                if _numbers_equal(value, float(fact["value"]))
            ]
            start = max(0, match.start() - 85)
            end = min(len(normalized), match.end() + 115)
            mentions.append(
                {
                    "page": page_number,
                    "value": value,
                    "raw_value": raw_value,
                    "unit": unit,
                    "text": match.group(0),
                    "snippet": normalized[start:end],
                    "match_start": match.start() - start,
                    "match_end": match.end() - start,
                    "mapped_paths": mapped_paths,
                }
            )
    return mentions
