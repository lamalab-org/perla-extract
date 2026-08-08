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
    r"(?<![\w.−–-])"
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


def flatten_facts(
    value: Any, path: str = "", context: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Flatten non-null scalar values into stable JSON Pointer-style facts."""
    facts: list[dict[str, Any]] = []
    if isinstance(value, dict):
        local_context = list(context)
        for key in ("name", "formula", "method", "step_name", "functionality"):
            candidate = value.get(key)
            if isinstance(candidate, str) and 2 <= len(candidate.strip()) <= 80:
                local_context.append(candidate.strip())
        unit = value.get("unit")
        if isinstance(unit, str) and unit.strip():
            local_context.append(f"unit:{unit.strip()}")
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            facts.extend(
                flatten_facts(child, f"{path}/{escaped}", tuple(local_context))
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            facts.extend(flatten_facts(child, f"{path}/{index}", context))
    elif value is not None:
        facts.append(
            {
                "path": path or "/",
                "value": value,
                "value_type": type(value).__name__,
                "context": list(dict.fromkeys(context)),
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
        variants = {str(value), f"{value:g}", f"{value:.2f}", f"{value:.3f}"}
        variants.update(item.replace(".", ",") for item in list(variants))
        return sorted((item for item in variants if len(item) >= 1), key=len, reverse=True)
    text = str(value).strip()
    if 2 <= len(text) <= 80:
        return [text]
    return []


FIELD_CONTEXT_TERMS = {
    "pce_at_the_start_of_the_experiment": (
        "initial pce", "initial efficiency", "initial value", "stability",
    ),
    "pce_at_the_end_of_experiment": (
        "final pce", "final efficiency", "retained", "after", "stability",
    ),
    "pce_after_1000_hours": ("1000 h", "1000 hours", "retained", "stability"),
    "pce_t80": ("t80", "80%", "lifetime", "stability"),
    "pce": (
        "pce", "power conversion", "efficiency", "champion", "device",
        "reverse scan", "forward scan", "stabilized", "aperture area",
        "voc", "jsc", "fill factor",
    ),
    "jsc": (
        "jsc", "short-circuit", "current density", "photocurrent", "averaged",
    ),
    "voc": ("voc", "open-circuit", "open circuit", "voltage", "averaged"),
    "ff": ("fill factor", "ff", "photovoltaic"),
    "active_area": ("active area", "aperture area", "device area", "area"),
    "bandgap": ("bandgap", "band gap", "eg", "ev"),
    "temperature": ("temperature", "anneal", "heated", "°c", " k"),
    "duration": ("duration", "anneal", "time", "hours", "min", "seconds"),
    "formula": ("composition", "perovskite", "formula", "absorber"),
    "number_devices": ("devices", "samples", "batch", "statistics"),
}


def _fact_context(fact: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    lowered = fact["path"].lower()
    key = next(
        (name for name in FIELD_CONTEXT_TERMS if f"/{name}" in lowered),
        "",
    )
    inherited = tuple(
        item.lower()
        for item in fact.get("context", [])
        if not str(item).startswith("unit:")
    )
    return key, (*FIELD_CONTEXT_TERMS.get(key, ()), *inherited)


def _expected_unit(fact: dict[str, Any]) -> str:
    return next(
        (
            str(item).removeprefix("unit:").lower()
            for item in fact.get("context", [])
            if str(item).startswith("unit:")
        ),
        "",
    )


def _context_term_present(term: str, text: str) -> bool:
    if len(term) <= 3 and term.isalnum():
        return bool(re.search(rf"\b{re.escape(term)}\b", text))
    return term in text


def _quote_window(text: str, start: int, end: int) -> tuple[str, int, int]:
    """Return a compact sentence-like passage around one exact match."""
    left_limit = max(0, start - 150)
    right_limit = min(len(text), end + 320)
    left_candidates = [text.rfind(mark, left_limit, start) for mark in (". ", "? ", "! ", "; ")]
    left_boundary = max(left_candidates)
    prefix = ""
    if left_boundary >= left_limit:
        quote_start = left_boundary + 2
    else:
        next_space = text.find(" ", left_limit, start)
        quote_start = next_space + 1 if next_space >= 0 else left_limit
        prefix = "…"
    right_candidates = [
        position
        for mark in (". ", "? ", "! ", "; ")
        if (position := text.find(mark, end, right_limit)) >= 0
    ]
    suffix = ""
    if right_candidates:
        quote_end = min(right_candidates) + 1
    else:
        last_space = text.rfind(" ", end, right_limit)
        quote_end = last_space if last_space >= end else right_limit
        suffix = "…" if quote_end < len(text) else ""
    raw = text[quote_start:quote_end]
    leading = len(raw) - len(raw.lstrip())
    snippet = prefix + raw.strip() + suffix
    offset = len(prefix) - leading
    return snippet, start - quote_start + offset, end - quote_start + offset


def suggestion_for_fact(
    pages: tuple[str, ...], fact: dict[str, Any]
) -> dict[str, Any] | None:
    """Rank exact-text evidence using field-specific surrounding context."""
    context_key, context_terms = _fact_context(fact)
    expected_unit = _expected_unit(fact)
    candidates = []
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
            for match in pattern.finditer(normalized):
                context_start = max(0, match.start() - 180)
                context_end = min(len(normalized), match.end() + 220)
                nearby = normalized[context_start:context_end].lower()
                matched_terms = [
                    term
                    for term in context_terms
                    if _context_term_present(term, nearby)
                ]
                score = 3 * len(matched_terms)
                immediate = normalized[
                    max(0, match.start() - 25):min(len(normalized), match.end() + 35)
                ].lower()
                if expected_unit:
                    score += 4 if expected_unit in immediate else -3
                inherited_terms = {
                    item.lower()
                    for item in fact.get("context", [])
                    if not str(item).startswith("unit:")
                }
                if inherited_terms and not inherited_terms.intersection(matched_terms):
                    score -= 2
                if re.search(r"\b(references|bibliography)\b", nearby):
                    score -= 5
                if re.search(r"\bdoi\b|https?://|\bet al\.?,?\s+\d{4}", nearby):
                    score -= 2
                snippet, snippet_start, snippet_end = _quote_window(
                    normalized, match.start(), match.end()
                )
                candidates.append({
                    "page": page_number,
                    "query": variant,
                    "snippet": snippet,
                    "match_start": snippet_start,
                    "match_end": snippet_end,
                    "score": score,
                    "rationale": (
                        f"Nearby {context_key.replace('_', ' ')} context: "
                        + ", ".join(matched_terms[:3])
                    )
                    if matched_terms
                    else "Exact value match; verify its device context",
                })
    return max(candidates, key=lambda item: (item["score"], -item["page"])) if candidates else None


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
        context_key, context_terms = _fact_context(fact)
        cache_key = (
            type(value), value, context_key, context_terms, _expected_unit(fact)
        )
        if cache_key not in cache:
            cache[cache_key] = suggestion_for_fact(pages, fact)
        if cache[cache_key]:
            suggestions[fact["path"]] = cache[cache_key]
    return suggestions


def _numbers_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-5, abs_tol=max(1e-4, abs(right) * 1e-5))


def _quantity_category(context: str) -> tuple[str, int]:
    lowered = context.lower()
    categories = (
        ("stability", 100, ("stability", "stable", "retained", "degradation", "maximum power point", "mpp", "t80", "illumination")),
        ("device performance", 90, ("pce", "efficiency", "fill factor", "jsc", "voc", "open-circuit", "short-circuit", "champion device")),
        ("device geometry", 65, ("active area", "aperture area", "device area", "thickness", "module area")),
        ("composition / process", 55, ("precursor", "solution", "spin-coated", "blade coated", "anneal", "concentration", "dissolved", "rpm")),
    )
    for category, priority, terms in categories:
        if any(term in lowered for term in terms):
            return category, priority
    if re.search(r"\b(fig(?:ure)?|table)\s*[s]?\d+", lowered):
        return "figure / table context", 25
    return "other quantity", 10


def _parse_quantity_value(raw_value: str) -> float:
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", raw_value):
        return float(raw_value.replace(",", ""))
    return float(raw_value.replace(",", "."))


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
                value = _parse_quantity_value(raw_value)
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
            context, _, _ = _quote_window(normalized, match.start(), match.end())
            category, priority = _quantity_category(context)
            mentions.append(
                {
                    "page": page_number,
                    "value": value,
                    "raw_value": raw_value,
                    "offset": match.start(),
                    "unit": unit,
                    "text": match.group(0),
                    "snippet": normalized[start:end],
                    "context": context,
                    "category": category,
                    "priority": priority,
                    "match_start": match.start() - start,
                    "match_end": match.end() - start,
                    "mapped_paths": mapped_paths,
                }
            )
    return mentions


def group_quantity_mentions(mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group nearby unmapped quantities into short, ranked review candidates."""
    groups: list[dict[str, Any]] = []
    for mention in sorted(mentions, key=lambda item: (item["page"], item.get("offset", 0))):
        previous = groups[-1] if groups else None
        same_passage = (
            previous
            and previous["page"] == mention["page"]
            and mention.get("offset", 0) - previous["last_offset"] <= 240
        )
        if same_passage:
            group = previous
        else:
            group = {
                "page": mention["page"],
                "context": mention.get("context") or mention["snippet"],
                "category": mention.get("category", "other quantity"),
                "priority": mention.get("priority", 0),
                "last_offset": mention.get("offset", 0),
                "mentions": [],
            }
            groups.append(group)
        group["mentions"].append(mention)
        group["last_offset"] = mention.get("offset", group["last_offset"])
        if len(mention.get("context", "")) > len(group["context"]):
            group["context"] = mention["context"]
        if mention.get("priority", 0) > group["priority"]:
            group["priority"] = mention["priority"]
            group["category"] = mention["category"]
    return sorted(
        ({key: value for key, value in group.items() if key != "last_offset"} for group in groups),
        key=lambda item: (-item["priority"], item["page"], item["context"]),
    )
