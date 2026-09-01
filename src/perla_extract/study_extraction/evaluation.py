"""Deterministically score rich study extractions against adjudicated truth.

The evaluator keeps inventory, record matching, and atomic-value agreement separate.
IDs are not compared because independent extraction runs legitimately assign different
identifiers. Evidence is scored by the extraction validator, not as record content.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, Field

from .models import (
    ReportedValue,
    StrictModel,
    StudyExtraction,
    study_schema_sha256,
)
from .units import convert_reported_value

EVALUATION_FORMAT_VERSION: Final[Literal[1]] = 1
MATCHER_VERSION: Final[Literal["rich-study-hungarian-v1"]] = "rich-study-hungarian-v1"
RecordKind = Literal[
    "device_families",
    "individual_devices",
    "performance_observations",
    "population_statistics",
    "stability_tests",
]
RECORD_KINDS: tuple[RecordKind, ...] = (
    "device_families",
    "individual_devices",
    "performance_observations",
    "population_statistics",
    "stability_tests",
)
RECORD_ID_FIELDS: dict[RecordKind, str] = {
    "device_families": "family_id",
    "individual_devices": "device_id",
    "performance_observations": "observation_id",
    "population_statistics": "population_id",
    "stability_tests": "test_id",
}


class EvaluationConfig(StrictModel):
    """Freeze tolerances that can change a reported benchmark result."""

    minimum_record_similarity: float = Field(default=0.35, ge=0, le=1)
    numeric_relative_tolerance: float = Field(default=0.01, ge=0)
    numeric_absolute_tolerance: float = Field(default=1e-9, ge=0)


class PRF(StrictModel):
    """Report the counts behind precision, recall, and F1."""

    predicted: int = Field(ge=0)
    truth: int = Field(ge=0)
    matched: int = Field(ge=0)
    precision: float | None = Field(ge=0, le=1)
    recall: float | None = Field(ge=0, le=1)
    f1: float | None = Field(ge=0, le=1)


class RecordMatch(StrictModel):
    """Expose one matcher decision so scores remain auditable."""

    kind: RecordKind
    truth_id: str
    predicted_id: str
    similarity: float = Field(ge=0, le=1)


class FieldAgreement(StrictModel):
    """Separate conditional field accuracy from end-to-end atomic-value recall."""

    scalar_fields_compared: int = Field(ge=0)
    scalar_fields_agreed: int = Field(ge=0)
    scalar_field_accuracy: float | None = Field(ge=0, le=1)
    relationships_compared: int = Field(ge=0)
    relationships_agreed: int = Field(ge=0)
    relationship_accuracy: float | None = Field(ge=0, le=1)
    reported_values: PRF
    matched_reported_values_with_equal_value: int = Field(ge=0)
    reported_value_accuracy: float | None = Field(ge=0, le=1)


class BenchmarkProvenance(StrictModel):
    """Identify the immutable truth item and split used for one paper score."""

    paper_id: str
    split: str
    ground_truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(
        default_factory=list
    )


class EvaluationValidationIssue(StrictModel):
    """Retain one deterministic prediction-validation failure in a score report."""

    path: str
    reason: str


class PredictionValidation(StrictModel):
    """Attach evidence and relationship validation when a complete run is scored."""

    status: Literal["verified", "needs_review"]
    counts: dict[str, object]
    issues: list[EvaluationValidationIssue]


class RunEfficiency(StrictModel):
    """Retain the measured resource use needed for quality/cost comparisons."""

    status: str
    live_calls: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    provider_requests: int | None = Field(default=None, ge=0)
    cost_tracking_complete: bool | None = None
    elapsed_seconds: float = Field(ge=0)


class EvaluationReport(StrictModel):
    """Represent a reproducible score without collapsing distinct error modes."""

    format_version: Literal[1] = EVALUATION_FORMAT_VERSION
    matcher_version: Literal["rich-study-hungarian-v1"] = MATCHER_VERSION
    study_schema_sha256: str
    benchmark: BenchmarkProvenance | None = None
    prediction_validation: PredictionValidation | None = None
    run_efficiency: RunEfficiency | None = None
    config: EvaluationConfig
    ignored_truth_record_keys: list[str]
    ignored_prediction_record_keys: list[str]
    inventory: dict[RecordKind, PRF]
    micro_inventory: PRF
    field_agreement: FieldAgreement
    matches: list[RecordMatch]
    unmatched_truth_record_keys: list[str]
    unmatched_prediction_record_keys: list[str]


class Agreement(StrictModel):
    """Expose numerator and denominator for one dataset-level agreement rate."""

    compared: int = Field(ge=0)
    agreed: int = Field(ge=0)
    accuracy: float | None = Field(ge=0, le=1)


class MetricSummary(StrictModel):
    """Summarize defined paper-level rates with a deterministic bootstrap interval."""

    paper_count: int = Field(ge=0)
    mean: float | None = Field(ge=0, le=1)
    ci95_lower: float | None = Field(ge=0, le=1)
    ci95_upper: float | None = Field(ge=0, le=1)


class DatasetPredictionValidation(StrictModel):
    """Summarize how many scored runs carried deterministic validation evidence."""

    paper_count: int = Field(ge=0)
    verified_papers: int = Field(ge=0)
    issue_count: int = Field(ge=0)


class DatasetEfficiency(StrictModel):
    """Sum measured run resources without inventing values for missing reports."""

    paper_count: int = Field(ge=0)
    live_calls: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    provider_request_papers: int = Field(ge=0)
    provider_requests: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class DatasetEvaluationReport(StrictModel):
    """Aggregate immutable paper reports without rerunning their record matcher."""

    format_version: Literal[1] = EVALUATION_FORMAT_VERSION
    matcher_version: Literal["rich-study-hungarian-v1"] = MATCHER_VERSION
    study_schema_sha256: str
    config: EvaluationConfig
    paper_count: int = Field(ge=1)
    split: str | None
    paper_ids: list[str]
    prediction_validation: DatasetPredictionValidation
    efficiency: DatasetEfficiency
    inventory_micro: dict[RecordKind, PRF]
    inventory_macro_f1: dict[RecordKind, MetricSummary]
    overall_micro: PRF
    overall_macro_f1: MetricSummary
    scalar_field_accuracy_macro: MetricSummary
    scalar_field_accuracy_micro: Agreement
    relationship_accuracy_macro: MetricSummary
    relationship_accuracy_micro: Agreement
    reported_values_micro: PRF
    reported_value_accuracy_macro: MetricSummary
    reported_value_accuracy_micro: Agreement


def _prf(predicted: int, truth: int, matched: int) -> PRF:
    """Calculate conventional counts and leave zero-denominator rates undefined."""

    precision = matched / predicted if predicted else None
    recall = matched / truth if truth else None
    f1 = None
    if predicted or truth:
        f1 = 2 * matched / (predicted + truth) if predicted + truth else 0.0
    return PRF(
        predicted=predicted,
        truth=truth,
        matched=matched,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _agreement(compared: int, agreed: int) -> Agreement:
    """Calculate an agreement rate while retaining its complete count basis."""

    return Agreement(
        compared=compared,
        agreed=agreed,
        accuracy=agreed / compared if compared else None,
    )


def _text(value: object) -> str:
    """Normalize presentation for comparison without interpreting chemical identity."""

    return re.sub(r"[^a-z0-9.+%-]+", " ", str(value).casefold()).strip()


def _tokens(values: Iterable[object]) -> set[str]:
    """Create transparent lexical features from source-backed record content."""

    return {
        token
        for value in values
        if value is not None
        for token in _text(value).split()
        if token
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    """Compare unordered lexical features and treat two empty sets as equal."""

    return len(left & right) / len(left | right) if left or right else 1.0


def _reported_values(value: object) -> list[ReportedValue]:
    """Collect atomic values recursively while ignoring evidence and identifiers."""

    found: list[ReportedValue] = []

    def walk(item: object) -> None:
        if isinstance(item, ReportedValue):
            found.append(item)
        elif isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                if name != "evidence":
                    walk(getattr(item, name))
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _record_features(record: object) -> set[str]:
    """Describe record content generically; stable IDs and quotations are excluded."""

    ignored = {
        "family_id",
        "device_id",
        "observation_id",
        "population_id",
        "test_id",
        "layer_id",
        "absorber_id",
        "step_id",
        "checkpoint_id",
        "target_layer_ids",
        "evidence",
        "value_number",
    }
    values: list[object] = []

    def walk(item: object, field: str | None = None) -> None:
        if field in ignored or item is None:
            return
        if isinstance(item, ReportedValue):
            values.extend((item.name, item.raw_value, item.unit))
        elif isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                walk(getattr(item, name), name)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, (str, int, float)):
            values.append(item)

    walk(record)
    return _tokens(values)


def _record_similarity(left: object, right: object) -> float:
    """Compare scientific content without relying on run-local record identifiers."""

    lexical = _jaccard(_record_features(left), _record_features(right))
    left_values = _reported_values(left)
    right_values = _reported_values(right)
    value_names = _jaccard(
        _tokens(value.name for value in left_values),
        _tokens(value.name for value in right_values),
    )
    return 0.75 * lexical + 0.25 * value_names


def _maximum_assignment(scores: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Return a deterministic maximum-weight one-to-one assignment.

    This is the rectangular Hungarian algorithm with zero-weight dummy entries. It
    avoids greedy order effects when several similar device variants compete for a
    match, without adding a heavy numerical dependency.
    """

    if not scores or not scores[0]:
        return []
    rows, columns = len(scores), len(scores[0])
    size = max(rows, columns)
    maximum = max(max(row) for row in scores)
    cost = [
        [
            maximum - (scores[i][j] if i < rows and j < columns else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    p = [0] * (size + 1)
    way = [0] * (size + 1)
    for i in range(1, size + 1):
        p[0] = i
        minimum = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        column = 0
        while True:
            used[column] = True
            row = p[column]
            delta = float("inf")
            next_column = 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                current = cost[row - 1][candidate - 1] - u[row] - v[candidate]
                if current < minimum[candidate]:
                    minimum[candidate] = current
                    way[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    u[p[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if p[column] == 0:
                break
        while True:
            previous = way[column]
            p[column] = p[previous]
            column = previous
            if column == 0:
                break
    pairs = [(p[column] - 1, column - 1) for column in range(1, size + 1)]
    return [(row, column) for row, column in pairs if row < rows and column < columns]


def _match(
    truth: Sequence[object],
    prediction: Sequence[object],
    threshold: float,
    similarity: Callable[[object, object], float] = _record_similarity,
) -> list[tuple[int, int, float]]:
    """Match records globally and discard assignments below the frozen threshold."""

    scores = [
        [
            score if (score := similarity(left, right)) >= threshold else 0.0
            for right in prediction
        ]
        for left in truth
    ]
    return [
        (left, right, scores[left][right])
        for left, right in _maximum_assignment(scores)
        if scores[left][right] > 0
    ]


def _record_id(kind: RecordKind, record: object) -> str:
    """Read the stable identifier field declared for one collection."""

    return str(getattr(record, RECORD_ID_FIELDS[kind]))


def _record_key(kind: RecordKind, record: object) -> str:
    """Use the same collection:id syntax as review-workbench decisions."""

    return f"{kind}:{_record_id(kind, record)}"


def _scalar_items(record: object) -> list[tuple[str, object]]:
    """Collect order-independent scalar facts outside values and relationships."""

    ignored = {
        "family_id",
        "device_id",
        "observation_id",
        "population_id",
        "test_id",
        "layer_id",
        "absorber_id",
        "step_id",
        "checkpoint_id",
        "target_layer_ids",
        "evidence",
        "value_number",
        "raw_value",
        "unit",
        "name",
    }
    result: list[tuple[str, object]] = []

    def walk(item: object, path: str = "") -> None:
        if isinstance(item, ReportedValue):
            return
        if isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                if name not in ignored:
                    walk(getattr(item, name), f"{path}.{name}" if path else name)
        elif isinstance(item, list):
            for child in item:
                walk(child, f"{path}[]")
        elif item is not None and isinstance(item, (str, int, float, bool)):
            comparable = _text(item) if isinstance(item, str) else item
            result.append((path, comparable))

    walk(record)
    return result


def _scalar_agreement(truth: object, prediction: object) -> tuple[int, int]:
    """Compare scalar multisets without treating list order as scientific meaning."""

    expected = Counter(_scalar_items(truth))
    actual = Counter(_scalar_items(prediction))
    compared = max(expected.total(), actual.total())
    agreed = (expected & actual).total()
    return compared, agreed


def _relationship_agreement(
    kind: RecordKind,
    truth: object,
    prediction: object,
    id_maps: dict[RecordKind, dict[str, str]],
) -> tuple[int, int]:
    """Score whether matched children point to their matched scientific parents."""

    fields = cast(
        tuple[tuple[str, RecordKind], ...],
        {
            "individual_devices": (("family_id", "device_families"),),
            "performance_observations": (("device_id", "individual_devices"),),
            "population_statistics": (("family_id", "device_families"),),
            "stability_tests": (
                ("family_id", "device_families"),
                ("device_id", "individual_devices"),
            ),
        }.get(kind, ()),
    )
    compared = 0
    agreed = 0
    for field, parent_kind in fields:
        expected_id = getattr(truth, field)
        actual_id = getattr(prediction, field)
        if expected_id is None and actual_id is None:
            continue
        compared += 1
        mapped_id = id_maps[parent_kind].get(expected_id) if expected_id else None
        agreed += int(mapped_id == actual_id)
    return compared, agreed


def _numeric_equal(
    truth: ReportedValue, prediction: ReportedValue, config: EvaluationConfig
) -> bool:
    """Compare normalized quantities through Pint when truth supplies a target unit."""

    if truth.value_number is None or prediction.value_number is None:
        return _text(truth.raw_value) == _text(prediction.raw_value)
    predicted_number = prediction.value_number
    if truth.unit:
        converted = convert_reported_value(prediction, truth.unit)
        if converted is None:
            return False
        predicted_number = converted
    scale = max(abs(truth.value_number), abs(predicted_number))
    return abs(truth.value_number - predicted_number) <= max(
        config.numeric_absolute_tolerance,
        config.numeric_relative_tolerance * scale,
    )


def _value_similarity(left: object, right: object) -> float:
    """Match atomic quantities primarily by their source-reported semantic names."""

    assert isinstance(left, ReportedValue) and isinstance(right, ReportedValue)
    name = _jaccard(_tokens([left.name]), _tokens([right.name]))
    raw = _jaccard(_tokens([left.raw_value]), _tokens([right.raw_value]))
    return 0.8 * name + 0.2 * raw


def evaluate_study(
    truth: StudyExtraction,
    prediction: StudyExtraction,
    *,
    ignored_truth_record_keys: Iterable[str] = (),
    benchmark: BenchmarkProvenance | None = None,
    prediction_validation: PredictionValidation | None = None,
    run_efficiency: RunEfficiency | None = None,
    config: EvaluationConfig | None = None,
) -> EvaluationReport:
    """Score one prediction while masking explicitly uncertain adjudications.

    Certain truth records are matched first. Remaining predictions that match an
    uncertain truth record are excluded rather than counted as false positives. This
    prevents reviewer abstentions from becoming either positive or negative labels.
    """

    config = config or EvaluationConfig()
    ignored = set(ignored_truth_record_keys)
    known_truth_keys = {
        _record_key(kind, record)
        for kind in RECORD_KINDS
        for record in getattr(truth, kind)
    }
    unknown_ignored = sorted(ignored - known_truth_keys)
    if unknown_ignored:
        raise ValueError(
            f"uncertainty mask references unknown truth records: {unknown_ignored}"
        )
    inventory: dict[RecordKind, PRF] = {}
    matches: list[RecordMatch] = []
    unmatched_truth: list[str] = []
    unmatched_prediction: list[str] = []
    ignored_predictions: list[str] = []
    matched_records: list[tuple[RecordKind, object, object]] = []
    total_truth_values = 0
    total_prediction_values = 0

    for kind in RECORD_KINDS:
        truth_records = list(getattr(truth, kind))
        prediction_records = list(getattr(prediction, kind))
        certain = [
            item for item in truth_records if _record_key(kind, item) not in ignored
        ]
        uncertain = [
            item for item in truth_records if _record_key(kind, item) in ignored
        ]
        certain_pairs = _match(
            certain,
            prediction_records,
            config.minimum_record_similarity,
        )
        used_prediction = {right for _, right, _ in certain_pairs}
        remaining_prediction_indexes = [
            index
            for index in range(len(prediction_records))
            if index not in used_prediction
        ]
        uncertain_pairs = _match(
            uncertain,
            [prediction_records[index] for index in remaining_prediction_indexes],
            config.minimum_record_similarity,
        )
        ignored_local = {
            remaining_prediction_indexes[right] for _, right, _ in uncertain_pairs
        }
        total_truth_values += sum(len(_reported_values(record)) for record in certain)
        total_prediction_values += sum(
            len(_reported_values(record))
            for index, record in enumerate(prediction_records)
            if index not in ignored_local
        )
        for index in sorted(ignored_local):
            ignored_predictions.append(_record_key(kind, prediction_records[index]))
        scored_prediction = len(prediction_records) - len(ignored_local)
        inventory[kind] = _prf(scored_prediction, len(certain), len(certain_pairs))
        matched_truth = {left for left, _, _ in certain_pairs}
        matched_prediction = {right for _, right, _ in certain_pairs}
        unmatched_truth.extend(
            _record_key(kind, record)
            for index, record in enumerate(certain)
            if index not in matched_truth
        )
        unmatched_prediction.extend(
            _record_key(kind, record)
            for index, record in enumerate(prediction_records)
            if index not in matched_prediction and index not in ignored_local
        )
        for left, right, similarity in certain_pairs:
            truth_record = certain[left]
            predicted_record = prediction_records[right]
            matched_records.append((kind, truth_record, predicted_record))
            matches.append(
                RecordMatch(
                    kind=kind,
                    truth_id=_record_id(kind, truth_record),
                    predicted_id=_record_id(kind, predicted_record),
                    similarity=similarity,
                )
            )

    scalar_compared = 0
    scalar_agreed = 0
    relationships_compared = 0
    relationships_agreed = 0
    equal_values = 0
    matched_value_count = 0
    id_maps: dict[RecordKind, dict[str, str]] = {
        kind: {
            match.truth_id: match.predicted_id
            for match in matches
            if match.kind == kind
        }
        for kind in RECORD_KINDS
    }
    for kind, truth_record, predicted_record in matched_records:
        compared, agreed = _scalar_agreement(truth_record, predicted_record)
        scalar_compared += compared
        scalar_agreed += agreed
        compared, agreed = _relationship_agreement(
            kind, truth_record, predicted_record, id_maps
        )
        relationships_compared += compared
        relationships_agreed += agreed
        expected = _reported_values(truth_record)
        actual = _reported_values(predicted_record)
        value_pairs = _match(expected, actual, 0.5, _value_similarity)
        matched_value_count += len(value_pairs)
        equal_values += sum(
            _numeric_equal(expected[left], actual[right], config)
            for left, right, _ in value_pairs
        )

    micro_predicted = sum(score.predicted for score in inventory.values())
    micro_truth = sum(score.truth for score in inventory.values())
    micro_matched = sum(score.matched for score in inventory.values())
    value_prf = _prf(
        total_prediction_values,
        total_truth_values,
        matched_value_count,
    )
    return EvaluationReport(
        study_schema_sha256=study_schema_sha256(),
        benchmark=benchmark,
        prediction_validation=prediction_validation,
        run_efficiency=run_efficiency,
        config=config,
        ignored_truth_record_keys=sorted(ignored),
        ignored_prediction_record_keys=sorted(ignored_predictions),
        inventory=inventory,
        micro_inventory=_prf(micro_predicted, micro_truth, micro_matched),
        field_agreement=FieldAgreement(
            scalar_fields_compared=scalar_compared,
            scalar_fields_agreed=scalar_agreed,
            scalar_field_accuracy=(
                scalar_agreed / scalar_compared if scalar_compared else None
            ),
            relationships_compared=relationships_compared,
            relationships_agreed=relationships_agreed,
            relationship_accuracy=(
                relationships_agreed / relationships_compared
                if relationships_compared
                else None
            ),
            reported_values=value_prf,
            matched_reported_values_with_equal_value=equal_values,
            reported_value_accuracy=(
                equal_values / matched_value_count if matched_value_count else None
            ),
        ),
        matches=matches,
        unmatched_truth_record_keys=sorted(unmatched_truth),
        unmatched_prediction_record_keys=sorted(unmatched_prediction),
    )


def _metric_summary(
    values: Iterable[float | None], *, bootstrap_samples: int, seed: int
) -> MetricSummary:
    """Calculate a mean and paper-resampling interval without numerical dependencies."""

    defined = [value for value in values if value is not None]
    if not defined:
        return MetricSummary(paper_count=0, mean=None, ci95_lower=None, ci95_upper=None)
    mean = sum(defined) / len(defined)
    if len(defined) == 1 or bootstrap_samples == 0:
        return MetricSummary(
            paper_count=len(defined),
            mean=mean,
            ci95_lower=mean,
            ci95_upper=mean,
        )
    generator = random.Random(seed)
    samples = sorted(
        sum(generator.choice(defined) for _ in defined) / len(defined)
        for _ in range(bootstrap_samples)
    )
    lower = samples[int(0.025 * (bootstrap_samples - 1))]
    upper = samples[int(0.975 * (bootstrap_samples - 1))]
    return MetricSummary(
        paper_count=len(defined),
        mean=mean,
        ci95_lower=lower,
        ci95_upper=upper,
    )


def aggregate_evaluations(
    reports: Sequence[EvaluationReport],
    *,
    bootstrap_samples: int = 2_000,
    seed: int = 0,
) -> DatasetEvaluationReport:
    """Aggregate compatible paper reports using counts and paper-level macro rates."""

    if not reports:
        raise ValueError("at least one evaluation report is required")
    if bootstrap_samples < 0:
        raise ValueError("bootstrap_samples cannot be negative")
    first = reports[0]
    incompatible = [
        index
        for index, report in enumerate(reports[1:], start=1)
        if report.format_version != first.format_version
        or report.study_schema_sha256 != first.study_schema_sha256
        or report.matcher_version != first.matcher_version
        or report.config != first.config
    ]
    if incompatible:
        raise ValueError(
            f"evaluation reports use incompatible schema, matcher, or config: {incompatible}"
        )
    provenance = [report.benchmark for report in reports]
    if any(item is None for item in provenance) and any(
        item is not None for item in provenance
    ):
        raise ValueError(
            "cannot mix provenance-verified and development evaluation reports"
        )
    verified = [item for item in provenance if item is not None]
    split: str | None = None
    paper_ids: list[str] = []
    if verified:
        splits = {item.split for item in verified}
        if len(splits) != 1:
            raise ValueError(
                f"evaluation reports mix benchmark splits: {sorted(splits)}"
            )
        split = verified[0].split
        paper_ids = [item.paper_id for item in verified]
        if len(set(paper_ids)) != len(paper_ids):
            raise ValueError(
                "evaluation reports contain a duplicate benchmark paper_id"
            )
        source_hashes: set[str] = set()
        duplicate_source = False
        for item in verified:
            identities = set(item.source_sha256) or {item.source_manifest_sha256}
            duplicate_source = duplicate_source or bool(source_hashes & identities)
            source_hashes.update(identities)
        if duplicate_source:
            raise ValueError(
                "evaluation reports contain duplicate source documents or manifests"
            )
    inventory_micro: dict[RecordKind, PRF] = {}
    inventory_macro: dict[RecordKind, MetricSummary] = {}
    for offset, kind in enumerate(RECORD_KINDS):
        scores = [report.inventory[kind] for report in reports]
        inventory_micro[kind] = _prf(
            sum(score.predicted for score in scores),
            sum(score.truth for score in scores),
            sum(score.matched for score in scores),
        )
        inventory_macro[kind] = _metric_summary(
            (score.f1 for score in scores),
            bootstrap_samples=bootstrap_samples,
            seed=seed + offset,
        )
    return DatasetEvaluationReport(
        study_schema_sha256=first.study_schema_sha256,
        config=first.config,
        paper_count=len(reports),
        split=split,
        paper_ids=paper_ids,
        prediction_validation=DatasetPredictionValidation(
            paper_count=sum(
                report.prediction_validation is not None for report in reports
            ),
            verified_papers=sum(
                report.prediction_validation is not None
                and report.prediction_validation.status == "verified"
                for report in reports
            ),
            issue_count=sum(
                len(report.prediction_validation.issues)
                for report in reports
                if report.prediction_validation is not None
            ),
        ),
        efficiency=DatasetEfficiency(
            paper_count=sum(report.run_efficiency is not None for report in reports),
            live_calls=sum(
                report.run_efficiency.live_calls
                for report in reports
                if report.run_efficiency is not None
            ),
            cache_hits=sum(
                report.run_efficiency.cache_hits
                for report in reports
                if report.run_efficiency is not None
            ),
            prompt_tokens=sum(
                report.run_efficiency.prompt_tokens
                for report in reports
                if report.run_efficiency is not None
            ),
            completion_tokens=sum(
                report.run_efficiency.completion_tokens
                for report in reports
                if report.run_efficiency is not None
            ),
            total_tokens=sum(
                report.run_efficiency.total_tokens
                for report in reports
                if report.run_efficiency is not None
            ),
            cost_usd=round(
                sum(
                    report.run_efficiency.cost_usd
                    for report in reports
                    if report.run_efficiency is not None
                ),
                8,
            ),
            provider_request_papers=sum(
                report.run_efficiency is not None
                and report.run_efficiency.provider_requests is not None
                for report in reports
            ),
            provider_requests=sum(
                report.run_efficiency.provider_requests or 0
                for report in reports
                if report.run_efficiency is not None
            ),
            elapsed_seconds=round(
                sum(
                    report.run_efficiency.elapsed_seconds
                    for report in reports
                    if report.run_efficiency is not None
                ),
                3,
            ),
        ),
        inventory_micro=inventory_micro,
        inventory_macro_f1=inventory_macro,
        overall_micro=_prf(
            sum(report.micro_inventory.predicted for report in reports),
            sum(report.micro_inventory.truth for report in reports),
            sum(report.micro_inventory.matched for report in reports),
        ),
        overall_macro_f1=_metric_summary(
            (report.micro_inventory.f1 for report in reports),
            bootstrap_samples=bootstrap_samples,
            seed=seed + 100,
        ),
        scalar_field_accuracy_macro=_metric_summary(
            (report.field_agreement.scalar_field_accuracy for report in reports),
            bootstrap_samples=bootstrap_samples,
            seed=seed + 101,
        ),
        scalar_field_accuracy_micro=_agreement(
            sum(report.field_agreement.scalar_fields_compared for report in reports),
            sum(report.field_agreement.scalar_fields_agreed for report in reports),
        ),
        relationship_accuracy_macro=_metric_summary(
            (report.field_agreement.relationship_accuracy for report in reports),
            bootstrap_samples=bootstrap_samples,
            seed=seed + 102,
        ),
        relationship_accuracy_micro=_agreement(
            sum(report.field_agreement.relationships_compared for report in reports),
            sum(report.field_agreement.relationships_agreed for report in reports),
        ),
        reported_values_micro=_prf(
            sum(report.field_agreement.reported_values.predicted for report in reports),
            sum(report.field_agreement.reported_values.truth for report in reports),
            sum(report.field_agreement.reported_values.matched for report in reports),
        ),
        reported_value_accuracy_macro=_metric_summary(
            (report.field_agreement.reported_value_accuracy for report in reports),
            bootstrap_samples=bootstrap_samples,
            seed=seed + 103,
        ),
        reported_value_accuracy_micro=_agreement(
            sum(report.field_agreement.reported_values.matched for report in reports),
            sum(
                report.field_agreement.matched_reported_values_with_equal_value
                for report in reports
            ),
        ),
    )
