"""Deterministically score rich study extractions against adjudicated truth.

The primary score requires correct scientific values in their recorded context.
Inventory, value recovery, and attribution diagnostics explain why credit was lost.
IDs are not compared because independent extractions assign different identifiers.
Citation validity is reported separately and does not establish scientific support.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import (
    DeviceFamily,
    IndividualDevice,
    ReportedValue,
    StrictModel,
    StudyExtraction,
    study_schema_sha256,
)
from .reference_release import ReferenceRelease
from .scoring_coverage import ReferenceCoverage
from .scoring_diagnostics import (
    FactDiagnostic,
    ScoringDiagnostics,
    diagnose_fact,
    summarize_diagnostics,
)
from .scoring_explanation import (
    ExplanationCollector,
    FactSelection,
    ScoreExplanation,
)
from .scoring_facts import (
    DEFAULT_OPERATION_ALIASES,
    FactContext,
    FactGroup,
    RecordReference,
    ScientificFact,
    UnorderedContext,
    compare_facts,
    equal_value,
    property_name,
    scientific_descriptor,
    scientific_facts,
    scientific_text,
)
from .scoring_fields import FACT_GROUPS, FACT_SUBFIELDS, FactSubfield
from .scoring_identity import (
    MeasurementUse,
    device_measurements,
    family_pair_evidence,
    measurement_subject,
    measurement_use_similarity,
    sample_code,
)
from .scoring_identity import record_similarity as _record_similarity
from .scoring_layers import align_layers
from .scoring_process import align_processes

EVALUATION_FORMAT_VERSION: Final[Literal[17]] = 17
MAX_FACT_CANDIDATES = 1_000_000
MATCHER_VERSION: Final[Literal["rich-study-hungarian-v23"]] = "rich-study-hungarian-v23"
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
PARENT_FIELDS: dict[RecordKind, tuple[tuple[str, RecordKind], ...]] = {
    "individual_devices": (("family_id", "device_families"),),
    "performance_observations": (("device_id", "individual_devices"),),
    "population_statistics": (("family_id", "device_families"),),
    "stability_tests": (
        ("family_id", "device_families"),
        ("device_id", "individual_devices"),
    ),
}


class DescriptorEquivalence(StrictModel):
    """Record a checked label equivalence for one immutable reference, not a guess.

    A reviewer must establish that these names denote the same cohort/specimen.
    The reason and exact input hash travel with every score using the rule. Rules
    cannot silently follow an edited reference or merge its distinct cohorts.
    """

    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_kind: Literal["population_statistics", "stability_tests"]
    reference_label: str = Field(min_length=1)
    alternative_labels: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("reference_label", "reason")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        value = scientific_text(value)
        if not value:
            raise ValueError("label and reason must not be blank")
        return value

    @field_validator("alternative_labels")
    @classmethod
    def distinct_labels(cls, values: list[str]) -> list[str]:
        values = [scientific_text(value) for value in values]
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("alternative labels must be nonempty and distinct")
        return values


class EvaluationConfig(StrictModel):
    """Freeze tolerances that can change a reported benchmark result."""

    minimum_record_similarity: float = Field(default=0.35, ge=0, le=1)
    numeric_relative_tolerance: float = Field(default=0.01, ge=0)
    numeric_absolute_tolerance: float = Field(default=0.0, ge=0)
    operation_aliases: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_OPERATION_ALIASES)
    )
    descriptor_equivalences: list[DescriptorEquivalence] = Field(default_factory=list)

    @field_validator("descriptor_equivalences")
    @classmethod
    def unambiguous_descriptors(
        cls, rules: list[DescriptorEquivalence]
    ) -> list[DescriptorEquivalence]:
        owners: dict[tuple[str, str, str], str] = {}
        for rule in rules:
            for label in [rule.reference_label, *rule.alternative_labels]:
                key = (rule.reference_sha256, rule.record_kind, label)
                if key in owners and owners[key] != rule.reference_label:
                    raise ValueError(
                        "descriptor equivalences must not overlap or form chains"
                    )
                owners[key] = rule.reference_label
        return rules

    @field_validator("operation_aliases")
    @classmethod
    def flat_operation_aliases(cls, values: dict[str, str]) -> dict[str, str]:
        """Make equivalence rules deterministic and auditable, without alias chains."""

        normalized: dict[str, str] = {}
        for key, value in values.items():
            key, value = (
                scientific_text(key).casefold(),
                scientific_text(value).casefold(),
            )
            if not key or not value or (key in normalized and normalized[key] != value):
                raise ValueError(
                    "operation aliases contain empty or conflicting entries"
                )
            normalized[key] = value
        if any(
            value in normalized and normalized[value] != value
            for value in normalized.values()
        ):
            raise ValueError(
                "operation aliases must map directly to canonical names, without chains or cycles"
            )
        return normalized


class PRF(StrictModel):
    """Report the counts behind precision, recall, and F1."""

    predicted: int = Field(ge=0)
    truth: int = Field(ge=0)
    matched: int = Field(ge=0)
    precision: float | None = Field(ge=0, le=1)
    recall: float | None = Field(ge=0, le=1)
    f1: float | None = Field(ge=0, le=1)

    @model_validator(mode="after")
    def rates_follow_counts(self) -> PRF:
        """Reject damaged reports before counts and rates disagree in aggregation."""

        if self.matched > min(self.predicted, self.truth):
            raise ValueError("matched count cannot exceed predicted or reference count")
        expected = {
            "precision": self.matched / self.predicted if self.predicted else None,
            "recall": self.matched / self.truth if self.truth else None,
            "f1": 2 * self.matched / (self.predicted + self.truth)
            if self.predicted + self.truth
            else None,
        }
        for name, rate in expected.items():
            actual = getattr(self, name)
            if (rate is None) != (actual is None) or (
                rate is not None
                and actual is not None
                and not math.isclose(rate, actual, rel_tol=1e-12, abs_tol=0.0)
            ):
                raise ValueError(f"{name} does not agree with the saved counts")
        return self


class RecordMatch(StrictModel):
    """Expose one matcher decision so scores remain auditable."""

    kind: RecordKind
    truth_id: str
    predicted_id: str
    similarity: float = Field(ge=0, le=1)
    ambiguous: bool = False
    family_features: dict[str, list[str]] | None = None
    identity_evidence: dict[str, list[str]] | None = None


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
    coverage: ReferenceCoverage = Field(default_factory=ReferenceCoverage)
    split: str
    ground_truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(
        default_factory=list
    )
    evidence_version: int | None = Field(default=None, ge=1)
    evidence_document_sha256: Annotated[
        str | None, Field(pattern=r"^[0-9a-f]{64}$")
    ] = None


class EvaluationValidationIssue(StrictModel):
    """Retain one deterministic prediction-validation failure in a score report."""

    path: str
    reason: str
    category: Literal["structural", "citation", "unspecified"] = "unspecified"


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
    cost_sources: list[str] = Field(default_factory=list)
    provider_requests: int | None = Field(default=None, ge=0)
    cost_tracking_complete: bool | None = None
    token_tracking_complete: bool | None = None
    elapsed_seconds: float = Field(ge=0)


class FactMatch(StrictModel):
    """Identify the exact source fields that earned scientific credit."""

    group: FactGroup
    subfield: FactSubfield
    field: str
    stratum: str
    truth_path: str
    predicted_path: str
    truth_container: str
    predicted_container: str


class FactScoreView(StrictModel):
    """Expose one comparison rule with its complete counts and field-level audit."""

    groups: dict[FactGroup, PRF]
    subfields: dict[FactSubfield, PRF]
    fields: dict[str, PRF]
    strata: dict[str, PRF]
    micro: PRF
    matches: list[FactMatch]
    unmatched_truth_paths: list[str]
    unmatched_prediction_paths: list[str]

    @model_validator(mode="after")
    def consistent_ledger(self) -> FactScoreView:
        """Reject internally damaged reports before aggregation trusts their totals."""

        if set(self.groups) != set(FACT_GROUPS):
            raise ValueError("fact groups must contain every scientific group")
        counts = (
            sum(g.predicted for g in self.groups.values()),
            sum(g.truth for g in self.groups.values()),
            sum(g.matched for g in self.groups.values()),
        )
        if self.micro != _prf(*counts):
            raise ValueError("fact micro counts disagree with group totals")
        if set(self.subfields) != set(FACT_SUBFIELDS) or self.micro != _prf(
            *(
                sum(getattr(s, field) for s in self.subfields.values())
                for field in ("predicted", "truth", "matched")
            )
        ):
            raise ValueError(
                "fact subfields must partition the complete fact inventory"
            )
        matched_subfields = Counter(match.subfield for match in self.matches)
        if any(matched_subfields[k] != s.matched for k, s in self.subfields.items()):
            raise ValueError("fact match ledger disagrees with subfield counts")
        for category, attribute in ((self.fields, "field"), (self.strata, "stratum")):
            if self.micro != _prf(
                *(
                    sum(getattr(s, k) for s in category.values())
                    for k in ("predicted", "truth", "matched")
                )
            ):
                raise ValueError("field counts must partition the fact inventory")
            credited = Counter(getattr(m, attribute) for m in self.matches)
            if set(credited) - category.keys() or any(
                credited[k] != score.matched for k, score in category.items()
            ):
                raise ValueError("field counts disagree with the fact match ledger")
        matched_groups = Counter(match.group for match in self.matches)
        if any(matched_groups[g] != score.matched for g, score in self.groups.items()):
            raise ValueError("fact match ledger disagrees with group counts")
        for side, total, unmatched in (
            ("truth", self.micro.truth, self.unmatched_truth_paths),
            ("predicted", self.micro.predicted, self.unmatched_prediction_paths),
        ):
            matched = [getattr(m, f"{side}_path") for m in self.matches]
            if (
                len(set(matched)) != len(matched)
                or len(set(unmatched)) != len(unmatched)
                or set(matched) & set(unmatched)
                or len(matched) + len(unmatched) != total
            ):
                raise ValueError(
                    f"{side} fact paths are not a one-to-one complete ledger"
                )
        return self


class ScoringIssue(StrictModel):
    """Make unresolved alignment visible without erasing facts from denominators."""

    kind: Literal[
        "ambiguous_record",
        "ambiguous_scope",
        "unmatched_records",
        "conflicting_representations",
    ]
    side: Literal["truth", "prediction"]
    paths: list[str]
    reason: str


class CoreFactScore(FactScoreView):
    """Correctly attributed claims, with recovery over the identical fact inventory."""

    profile: Literal["core-scientific-facts-v20"] = "core-scientific-facts-v20"
    recovery: FactScoreView
    scoring_status: Literal["ready", "needs_review"]
    issues: list[ScoringIssue]
    fact_sources: dict[Literal["truth", "prediction"], dict[str, list[str]]]
    diagnostics: ScoringDiagnostics

    @model_validator(mode="after")
    def consistent_views(self) -> CoreFactScore:
        """All views score the same facts; strict credit requires value and context."""

        view = self.recovery
        for group, strict in self.groups.items():
            diagnostic = view.groups[group]
            if (strict.truth, strict.predicted) != (
                diagnostic.truth,
                diagnostic.predicted,
            ) or strict.matched > diagnostic.matched:
                raise ValueError("fact views have inconsistent counts")
        for key, strict in self.subfields.items():
            diagnostic = view.subfields[key]
            if (strict.truth, strict.predicted) != (
                diagnostic.truth,
                diagnostic.predicted,
            ) or strict.matched > diagnostic.matched:
                raise ValueError("fact subfield views have inconsistent counts")
        if (self.scoring_status == "needs_review") != bool(self.issues):
            raise ValueError("scoring status disagrees with issues")
        if set(self.fact_sources) != {"truth", "prediction"}:
            raise ValueError("fact sources must cover both studies")
        for side, unmatched in (
            ("truth", self.unmatched_truth_paths),
            ("prediction", self.unmatched_prediction_paths),
        ):
            match_field = "truth_path" if side == "truth" else "predicted_path"
            paths = set(unmatched) | {getattr(m, match_field) for m in self.matches}
            sources = (
                self.fact_sources["truth"]
                if side == "truth"
                else self.fact_sources["prediction"]
            )
            if set(sources) != paths or any(
                path not in aliases for path, aliases in sources.items()
            ):
                raise ValueError("fact sources must identify every canonical fact")
            diagnostic_paths = set(
                view.unmatched_truth_paths
                if side == "truth"
                else view.unmatched_prediction_paths
            ) | {getattr(m, match_field) for m in view.matches}
            if diagnostic_paths != paths:
                raise ValueError("fact views must identify the same canonical facts")
            flattened = [path for aliases in sources.values() for path in aliases]
            if len(set(flattened)) != len(flattened):
                raise ValueError("a source field cannot belong to multiple facts")
            rows = self.diagnostics.facts["truth" if side == "truth" else "prediction"]
            if len(rows) != len(paths) or {r.path for r in rows} != paths:
                raise ValueError("diagnostics must cover the complete fact inventory")
            if sum(r.matched for r in rows) != self.micro.matched:
                raise ValueError("diagnostic credits disagree with the match ledger")
        if self.diagnostics != summarize_diagnostics(self.diagnostics.facts):
            raise ValueError("diagnostic coverage disagrees with its fact ledger")
        return self


class EvaluationReport(StrictModel):
    """Represent a reproducible score without collapsing distinct error modes."""

    format_version: Literal[17] = EVALUATION_FORMAT_VERSION
    matcher_version: Literal["rich-study-hungarian-v23"] = MATCHER_VERSION
    study_schema_sha256: str
    truth_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str | None = None
    run_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    benchmark: BenchmarkProvenance | None = None
    interpretation: Literal["reference_agreement", "reviewed_scope"] = (
        "reference_agreement"
    )
    prediction_validation: PredictionValidation | None = None
    run_efficiency: RunEfficiency | None = None
    input_warnings: list[str] = Field(default_factory=list)
    config: EvaluationConfig
    reference_policy: Literal["fixed-no-exclusions"] = "fixed-no-exclusions"
    inventory: dict[RecordKind, PRF]
    micro_inventory: PRF
    field_agreement: FieldAgreement
    core_facts: CoreFactScore
    matches: list[RecordMatch]
    unmatched_truth_record_keys: list[str]
    unmatched_prediction_record_keys: list[str]

    @model_validator(mode="after")
    def interpretation_matches_coverage(self) -> "EvaluationReport":
        complete = self.benchmark is not None and self.benchmark.coverage.complete()
        if (self.interpretation == "reviewed_scope") != complete:
            raise ValueError("Score interpretation disagrees with reference coverage")
        return self


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
    interval_status: Literal[
        "available", "no_values", "disabled", "insufficient_papers"
    ]


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
    cost_tracking_complete: bool
    token_tracking_complete: bool
    cost_complete_papers: int = Field(ge=0)
    cost_incomplete_papers: int = Field(ge=0)
    cost_unknown_papers: int = Field(ge=0)
    provider_request_papers: int = Field(ge=0)
    provider_requests: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class EvaluationInput(StrictModel):
    """Retain the reference and prediction behind each aggregated paper score."""

    benchmark: BenchmarkProvenance | None
    truth_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetEvaluationReport(StrictModel):
    """Aggregate immutable paper reports without rerunning their record matcher."""

    format_version: Literal[17] = EVALUATION_FORMAT_VERSION
    matcher_version: Literal["rich-study-hungarian-v23"] = MATCHER_VERSION
    study_schema_sha256: str
    config: EvaluationConfig
    bootstrap_samples: int = Field(ge=0)
    bootstrap_seed: int
    bootstrap_method: Literal["paper-percentile-95"] = "paper-percentile-95"
    paper_count: int = Field(ge=1)
    split: str | None
    paper_ids: list[str]
    reference_release_sha256: Annotated[
        str | None, Field(pattern=r"^[0-9a-f]{64}$")
    ] = None
    input_reports: list[EvaluationInput]
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
    core_fact_groups_micro: dict[FactGroup, PRF]
    core_fact_groups_macro_f1: dict[FactGroup, MetricSummary]
    core_fact_subfields_micro: dict[FactSubfield, PRF]
    core_fact_subfields_macro_f1: dict[FactSubfield, MetricSummary]
    core_fields_micro: dict[str, PRF]
    core_field_strata_micro: dict[str, PRF]
    core_facts_micro: PRF
    core_facts_mean_paper_f1: MetricSummary
    core_recovery_micro: PRF
    core_recovery_mean_paper_f1: MetricSummary
    papers_needing_scoring_review: int = Field(ge=0)
    scoring_issue_count: int = Field(ge=0)
    papers_with_unreviewed_scope: int = Field(ge=0)


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


def _maximum_assignment(scores: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Return maximum-weight one-to-one pairs, without ambiguity diagnostics."""

    return _assignment(scores)[0]


def _assignment(
    scores: Sequence[Sequence[float]], *, detect_ambiguity: bool = False
) -> tuple[list[tuple[int, int]], set[tuple[int, int]]]:
    """Return a deterministic maximum-weight one-to-one assignment.

    This is the rectangular Hungarian algorithm with zero-weight dummy entries. It
    avoids greedy order effects when several similar device variants compete for a
    match. When requested, dual reduced costs identify alternative optimal complete
    assignments: a selected edge is non-unique exactly when it lies on a tight
    alternating cycle. This avoids solving the optimization once per selected edge.
    """

    if not scores or not scores[0]:
        return [], set()
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
    real_pairs = [
        (row, column) for row, column in pairs if row < rows and column < columns
    ]
    ambiguous: set[tuple[int, int]] = set()
    if detect_ambiguity:
        # A non-matched tight edge i->j followed by column j's matched edge gives
        # a directed edge between rows. Cycles correspond to alternate optima.
        # Include dummy rows/columns: rectangular assignments can be ambiguous too.
        tolerance = 1e-12 / size
        edges = [
            [
                p[j + 1] - 1
                for j in range(size)
                if p[j + 1] - 1 != i
                and abs(cost[i][j] - u[i + 1] - v[j + 1]) <= tolerance
            ]
            for i in range(size)
        ]
        for row, column in real_pairs:
            pending = list(edges[row])
            visited: set[int] = set()
            while pending:
                current = pending.pop()
                if current == row:
                    ambiguous.add((row, column))
                    break
                if current not in visited:
                    visited.add(current)
                    pending.extend(edges[current])
    return real_pairs, ambiguous


def _match(
    truth: Sequence[object],
    prediction: Sequence[object],
    threshold: float,
    similarity: Callable[[object, object], float],
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


def _record_pairs(
    kind: RecordKind,
    truth: Sequence[object],
    prediction: Sequence[object],
    identities: list[tuple[RecordKind, str, str]],
    threshold: float,
    relative: float,
    absolute: float,
    measurement_uses: tuple[
        dict[str, frozenset[MeasurementUse]], dict[str, frozenset[MeasurementUse]]
    ]
    | None = None,
) -> list[tuple[int, int, float, bool]]:
    """Prefer established parent and protocol context over coincidentally equal numbers.

    A known parent contributes to candidate eligibility. A contextual bonus resolves assignments
    such as identical JV results from different devices without relying on list order.
    Incompatible pairs remain available for inventory/link-error diagnostics; they
    cannot earn contextual fact credit.
    """

    mapping = {(k, left): right for k, left, right in identities}
    fields = PARENT_FIELDS.get(kind, ())

    def score(left: object, right: object) -> float:
        # The schema stores cohort/standalone specimen identity in these labels.
        # Never let coincidentally equal outcomes override a different identity.
        if scientific_descriptor(left) != scientific_descriptor(right):
            return 0
        if kind in {"device_families", "individual_devices"}:
            left_code = (
                sample_code(left)
                if isinstance(left, (DeviceFamily, IndividualDevice))
                else None
            )
            right_code = (
                sample_code(right)
                if isinstance(right, (DeviceFamily, IndividualDevice))
                else None
            )
            if (
                left_code is not None
                and right_code is not None
                and left_code != right_code
            ):
                return 0
        content = _record_similarity(left, right, relative, absolute)
        if (
            kind == "device_families"
            and left_code is not None
            and left_code == right_code
        ):
            content = (content + 1.0) / 2
        compatible = any(
            getattr(left, field) is not None for field, _ in fields
        ) and all(
            (getattr(left, field) is None and getattr(right, field) is None)
            or (
                getattr(left, field) is not None
                and mapping.get((parent, getattr(left, field))) is not None
                and mapping[(parent, getattr(left, field))] == getattr(right, field)
            )
            for field, parent in fields
        )
        # A matched parent is independent identity evidence, not merely a tie
        # breaker after a lexical cutoff. Give content and an established parent
        # equal weight. Missing parents add no evidence; contradictory required
        # context is still rejected when facts are compared.
        content = (content + 1.0) / 2 if compatible else content
        if content < threshold:
            return 0
        if (
            kind == "individual_devices"
            and compatible
            and isinstance(left, IndividualDevice)
            and isinstance(right, IndividualDevice)
        ):
            # Explicit methods/scans corroborate otherwise identical specimen
            # descriptions. Outcomes and metric inventories supply no evidence.
            if measurement_uses is not None:
                a = measurement_uses[0].get(left.device_id, frozenset())
                b = measurement_uses[1].get(right.device_id, frozenset())
                overlap = measurement_use_similarity(a, b)
                content = (content + overlap) / 2
        protocol = all(
            getattr(left, field, None) == getattr(right, field, None)
            for field in (
                "measurement_type",
                "scan_direction",
                "statistic_type",
            )
        )
        return content + (2 if compatible and protocol else 0)

    scores = [[score(left, right) for right in prediction] for left in truth]
    pairs = []
    assignment, ambiguous = _assignment(scores, detect_ambiguity=True)
    for i, j in assignment:
        selected = scores[i][j]
        if selected <= 0:
            continue
        pairs.append(
            (
                i,
                j,
                selected - 2 if selected >= 2 else selected,
                (i, j) in ambiguous,
            )
        )
    return pairs


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

    fields = PARENT_FIELDS.get(kind, ())
    compared = 0
    agreed = 0
    for field, parent_kind in fields:
        expected_id = getattr(truth, field)
        actual_id = getattr(prediction, field)
        if expected_id is None and actual_id is None:
            continue
        compared += 1
        mapped_id = id_maps[parent_kind].get(expected_id) if expected_id else None
        agreed += int(
            expected_id is not None and mapped_id is not None and mapped_id == actual_id
        )
    return compared, agreed


def _numeric_equal(
    truth: ReportedValue, prediction: ReportedValue, config: EvaluationConfig
) -> bool:
    """Use the same conservative quantity equality in diagnostic and core scores."""

    return equal_value(
        truth,
        prediction,
        config.numeric_relative_tolerance,
        config.numeric_absolute_tolerance,
    )


def _core_fact_score(
    truth: StudyExtraction,
    prediction: StudyExtraction,
    identities: list[tuple[RecordKind, str, str]],
    config: EvaluationConfig,
    ambiguous_truth: set[str],
    ambiguous_prediction: set[str],
    explanation: ExplanationCollector | None = None,
) -> CoreFactScore:
    """Assign compatible concepts once per view, independent of storage containers.

    Subjects are paired before this step without outcomes. Recovery ignores their
    attribution; the primary view requires both value and scientific context.
    """

    truth_ids = {f"{kind}:{left}": f"{kind}:{left}" for kind, left, _ in identities}
    prediction_ids = {
        f"{kind}:{right}": f"{kind}:{left}" for kind, left, right in identities
    }
    families = [
        (left, right) for kind, left, right in identities if kind == "device_families"
    ]
    layers = align_layers(truth, prediction, families)
    processes = align_processes(
        truth,
        prediction,
        families,
        config.operation_aliases,
        layers.references,
    )
    projections = [
        scientific_facts(
            truth,
            truth_ids,
            "truth",
            config.operation_aliases,
            processes.references[0],
            layers.references[0],
        ),
        scientific_facts(
            prediction,
            prediction_ids,
            "prediction",
            config.operation_aliases,
            processes.references[1],
            layers.references[1],
        ),
    ]
    issues: list[ScoringIssue] = []
    for kind in RECORD_KINDS:
        if (
            getattr(truth, kind)
            and getattr(prediction, kind)
            and not any(k == kind for k, _, _ in identities)
        ):
            for side, study in (("truth", truth), ("prediction", prediction)):
                issues.append(
                    ScoringIssue(
                        kind="unmatched_records",
                        side=side,
                        paths=[
                            f"/{kind}/{i}" for i, _ in enumerate(getattr(study, kind))
                        ],
                        reason="Both studies contain this record type, but no owners paired. Inspect alignment before interpreting zero contextual credit as failed information recovery.",
                    )
                )
    ambiguous_prefixes: list[set[str]] = [
        set(a) | b for a, b in zip(processes.ambiguous, layers.ambiguous)
    ]
    for side, layer_paths in zip(("truth", "prediction"), layers.ambiguous):
        if layer_paths:
            issues.append(
                ScoringIssue(
                    kind="ambiguous_scope",
                    side=side,
                    paths=sorted(layer_paths),
                    reason="Alternative longest ordered material alignments change these layer links; roles, composition and outcomes cannot break the tie.",
                )
            )
    for side, process_paths in zip(("truth", "prediction"), processes.ambiguous):
        if process_paths:
            issues.append(
                ScoringIssue(
                    kind="ambiguous_scope",
                    side=side,
                    paths=sorted(process_paths),
                    reason="Alternative optimal order-preserving alignments change these step pairings; conditions cannot resolve them.",
                )
            )
    ambiguous_record_paths: dict[RecordReference, list[tuple[str, str]]] = {}
    for i, (side, study, keys, projection) in enumerate(
        zip(
            ("truth", "prediction"),
            (truth, prediction),
            (ambiguous_truth, ambiguous_prediction),
            projections,
        )
    ):
        for paths in projection.conflicts:
            ambiguous_prefixes[i].update(paths)
            issues.append(
                ScoringIssue(
                    kind="conflicting_representations",
                    side=side,
                    paths=list(paths),
                    reason="Linked material representations assert incompatible identities or values for the same claim.",
                )
            )
        for kind in RECORD_KINDS:
            for index, record in enumerate(getattr(study, kind)):
                if (
                    _record_key(kind, record) in keys
                    and measurement_subject(record) is None
                ):
                    path = f"/{kind}/{index}"
                    canonical = (truth_ids, prediction_ids)[i][
                        _record_key(kind, record)
                    ]
                    ambiguous_record_paths.setdefault(
                        RecordReference(canonical), []
                    ).append((side, path))
                    ambiguous_prefixes[i].add(path)
                    issues.append(
                        ScoringIssue(
                            kind="ambiguous_record",
                            side=side,
                            paths=[path],
                            reason="An alternative optimal complete assignment changes this record pairing.",
                        )
                    )
        for paths in projection.ambiguous_scopes(
            config.numeric_relative_tolerance, config.numeric_absolute_tolerance
        ):
            ambiguous_prefixes[i].update(paths)
            issues.append(
                ScoringIssue(
                    kind="ambiguous_scope",
                    side=side,
                    paths=list(paths),
                    reason="These nested objects have indistinguishable identities; outcomes cannot establish their identity.",
                )
            )
    ambiguous_ids = {
        RecordReference(truth_ids[k]) for k in ambiguous_truth if k in truth_ids
    } | {
        RecordReference(prediction_ids[k])
        for k in ambiguous_prediction
        if k in prediction_ids
    }

    def ambiguous_references(value: object) -> Iterable[RecordReference]:
        if isinstance(value, RecordReference):
            if value in ambiguous_ids:
                yield value
            return
        if isinstance(value, FactContext):
            value = value.values
        if isinstance(value, UnorderedContext):
            value = value.items
        if isinstance(value, tuple):
            for v in value:
                yield from ambiguous_references(v)

    blocked = [
        {
            f.path
            for f in projection.facts
            if any(ambiguous_references(f.context))
            or any(
                path == prefix or path.startswith(prefix + "/")
                for path in f.source_paths
                for prefix in prefixes
            )
        }
        for projection, prefixes in zip(projections, ambiguous_prefixes)
    ]
    expected, actual = (p.facts for p in projections)
    if explanation is not None:
        sources: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for i, (side, facts) in enumerate(
            (("truth", expected), ("prediction", actual))
        ):
            for fact in facts:
                if fact.path not in blocked[i]:
                    continue
                source_paths: set[tuple[str, str]] = {
                    (side, path)
                    for issue in issues
                    if issue.side == side
                    and any(
                        path == prefix or path.startswith(prefix + "/")
                        for path in fact.source_paths
                        for prefix in issue.paths
                    )
                    for path in issue.paths
                }
                for reference in ambiguous_references(fact.context):
                    source_paths.update(ambiguous_record_paths[reference])
                sources[(side, fact.path)] = sorted(source_paths)
        explanation.start(expected, actual, (blocked[0], blocked[1]), sources)
    buckets: dict[
        tuple[str, str],
        tuple[list[ScientificFact], list[ScientificFact]],
    ] = {}
    for side_index, facts in enumerate((expected, actual)):
        for fact in facts:
            buckets.setdefault(fact.concept, ([], []))[side_index].append(fact)
    strict_matches: list[FactMatch] = []
    value_matches: list[FactMatch] = []
    diagnostic_facts: dict[Literal["truth", "prediction"], list[FactDiagnostic]] = {
        "truth": [],
        "prediction": [],
    }
    for left, right in buckets.values():
        if len(left) * len(right) > MAX_FACT_CANDIDATES:
            raise ValueError(
                f"Scoring resource limit exceeded: {len(left)} x {len(right)} "
                f"candidates for {left[0].concept}; no truncated score was produced"
            )
        decisions = [
            [
                compare_facts(
                    a,
                    b,
                    config.numeric_relative_tolerance,
                    config.numeric_absolute_tolerance,
                    ambiguity_blocked=a.path in blocked[0] or b.path in blocked[1],
                    explain=explanation is not None
                    and explanation.selects_pair(a.path, b.path),
                )
                for b in right
            ]
            for a in left
        ]
        bucket_matches: dict[str, list[FactMatch]] = {}
        for mode, matches in (
            ("attributed", strict_matches),
            ("recovery", value_matches),
        ):
            # Identical binary weights and assignment as _match(..., threshold=1).
            # Retain each comparison once for all views and optional explanations.
            scores = [[float(d.eligible(mode)) for d in row] for row in decisions]
            bucket_matches[mode] = []
            for i, j in _maximum_assignment(scores):
                if scores[i][j] <= 0:
                    continue
                a, b = left[i], right[j]
                if (a.group, a.subfield) != (b.group, b.subfield):
                    raise ValueError(
                        f"Equivalent facts have inconsistent reporting bins: {a.path} and {b.path}; "
                        "fix the classifier rather than restricting semantic matches"
                    )
                bucket_matches[mode].append(
                    FactMatch(
                        group=a.group,
                        subfield=a.subfield,
                        field=a.field_key,
                        stratum=a.stratum,
                        truth_path=a.path,
                        predicted_path=b.path,
                        truth_container=a.path.split("/")[1],
                        predicted_container=b.path.split("/")[1],
                    )
                )
            matches.extend(bucket_matches[mode])
        matched_paths = (
            {m.truth_path for m in bucket_matches["attributed"]},
            {m.predicted_path for m in bucket_matches["attributed"]},
        )
        for side_index, (side, facts) in enumerate(
            (("truth", left), ("prediction", right))
        ):
            for i, fact in enumerate(facts):
                pairs = (
                    decisions[i] if side_index == 0 else [row[i] for row in decisions]
                )
                diagnostic_facts["truth" if side == "truth" else "prediction"].append(
                    diagnose_fact(
                        fact,
                        pairs,
                        fact.path in matched_paths[side_index],
                        ambiguous=fact.path in blocked[side_index],
                    )
                )
        if explanation is not None:
            explanation.capture_bucket(left, right, decisions, bucket_matches)
    strict = _fact_score_view(expected, actual, strict_matches)
    return CoreFactScore(
        **strict.model_dump(),
        recovery=_fact_score_view(expected, actual, value_matches),
        scoring_status="needs_review" if issues else "ready",
        issues=issues,
        diagnostics=summarize_diagnostics(diagnostic_facts),
        fact_sources={
            side: {fact.path: list(fact.source_paths) for fact in projection.facts}
            for side, projection in zip(("truth", "prediction"), projections)
        },
    )


def _fact_score_view(
    expected: list[ScientificFact],
    actual: list[ScientificFact],
    matches: list[FactMatch],
) -> FactScoreView:
    """Keep identical denominators across value, attribution and strict comparisons."""

    groups = {
        group: _prf(
            sum(f.group == group for f in actual),
            sum(f.group == group for f in expected),
            sum(m.group == group for m in matches),
        )
        for group in FACT_GROUPS
    }
    matched_left = {m.truth_path for m in matches}
    matched_right = {m.predicted_path for m in matches}
    return FactScoreView(
        groups=groups,
        fields=_field_counts(expected, actual, matches, "field_key", "field"),
        strata=_field_counts(expected, actual, matches, "stratum", "stratum"),
        subfields={
            key: _prf(
                sum(f.subfield == key for f in actual),
                sum(f.subfield == key for f in expected),
                sum(m.subfield == key for m in matches),
            )
            for key in FACT_SUBFIELDS
        },
        micro=_prf(len(actual), len(expected), len(matches)),
        matches=matches,
        unmatched_truth_paths=sorted(
            f.path for f in expected if f.path not in matched_left
        ),
        unmatched_prediction_paths=sorted(
            f.path for f in actual if f.path not in matched_right
        ),
    )


def _field_counts(
    expected: list[ScientificFact],
    actual: list[ScientificFact],
    matches: list[FactMatch],
    fact_key: str,
    match_key: str,
) -> dict[str, PRF]:
    """Derive field totals from the same facts and decisions as the overall score.

    Strata describe the reference measurement for a credited pair. Recovery can
    cross strata, so its predicted denominator is assigned to that reference
    stratum too; unmatched predictions retain their own reported stratum.
    """
    truth = Counter(getattr(f, fact_key) for f in expected)
    matched = Counter(getattr(m, match_key) for m in matches)
    assigned = {m.predicted_path: getattr(m, match_key) for m in matches}
    predicted = Counter(assigned.get(f.path, getattr(f, fact_key)) for f in actual)
    keys = truth.keys() | predicted.keys()
    if fact_key == "field_key":
        keys |= {"measurement:" + k for k in ("pce", "ff", "voc", "jsc")}
    return {k: _prf(predicted[k], truth[k], matched[k]) for k in sorted(keys)}


def _value_similarity(left: object, right: object) -> float:
    """Match atomic quantities primarily by their source-reported semantic names."""

    assert isinstance(left, ReportedValue) and isinstance(right, ReportedValue)
    name = _jaccard({property_name(left.name)}, {property_name(right.name)})
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
    run_id: str | None = None,
    run_manifest_sha256: str | None = None,
    config: EvaluationConfig | None = None,
    _explanation: ExplanationCollector | None = None,
) -> EvaluationReport:
    """Compare every reference record without prediction-dependent exclusions.

    Reviewer uncertainty must be resolved before benchmark export. The legacy
    exclusion argument is accepted only when empty so older callers fail explicitly
    instead of silently changing which facts contribute to a score.
    """

    config = config or EvaluationConfig()
    truth_digest, prediction_digest = _study_digest(truth), _study_digest(prediction)
    rules = [
        rule
        for rule in config.descriptor_equivalences
        if rule.reference_sha256 == truth_digest
    ]
    if rules:
        # Normalize scorer-local copies only. Hashes and diagnostic paths still
        # identify the original inputs; no reference or prediction is rewritten.
        prediction = prediction.model_copy(deep=True)
        for rule in rules:
            reference_labels = {
                scientific_descriptor(record)
                for record in getattr(truth, rule.record_kind)
            }
            if rule.reference_label not in reference_labels:
                raise ValueError(
                    "descriptor equivalence names no reference cohort/specimen"
                )
            if any(
                label in reference_labels and label != rule.reference_label
                for label in rule.alternative_labels
            ):
                raise ValueError(
                    "descriptor equivalence would merge distinct reference cohorts/specimens"
                )
            for record in getattr(prediction, rule.record_kind):
                if scientific_descriptor(record) in rule.alternative_labels:
                    field = (
                        "label"
                        if rule.record_kind == "population_statistics"
                        else "specimen_label"
                    )
                    setattr(record, field, rule.reference_label)
    # Identity maps are dictionaries. Reused IDs would silently collapse distinct
    # records and attach facts to the wrong device instead of measuring quality.
    for side, study in (("truth", truth), ("prediction", prediction)):
        for kind in RECORD_KINDS:
            counts = Counter(
                _record_id(kind, record) for record in getattr(study, kind)
            )
            duplicates = sorted(key for key, count in counts.items() if count > 1)
            if duplicates:
                raise ValueError(
                    f"{side}.{kind} contains duplicate record IDs: {', '.join(duplicates)}; "
                    "resolve the identities before scoring"
                )
    if tuple(ignored_truth_record_keys):
        raise ValueError(
            "reference exclusions are no longer supported; resolve uncertain records "
            "and export a finalized reference before scoring"
        )
    inventory: dict[RecordKind, PRF] = {}
    matches: list[RecordMatch] = []
    unmatched_truth: list[str] = []
    unmatched_prediction: list[str] = []
    matched_records: list[tuple[RecordKind, object, object]] = []
    identity_pairs: list[tuple[RecordKind, str, str]] = []
    ambiguous_truth: set[str] = set()
    ambiguous_prediction: set[str] = set()
    total_truth_values = 0
    total_prediction_values = 0
    measurement_uses = (device_measurements(truth), device_measurements(prediction))

    for kind in RECORD_KINDS:
        truth_records = list(getattr(truth, kind))
        prediction_records = list(getattr(prediction, kind))
        pairs = _record_pairs(
            kind,
            truth_records,
            prediction_records,
            identity_pairs,
            config.minimum_record_similarity,
            config.numeric_relative_tolerance,
            config.numeric_absolute_tolerance,
            measurement_uses=measurement_uses,
        )
        total_truth_values += sum(
            len(_reported_values(record)) for record in truth_records
        )
        total_prediction_values += sum(
            len(_reported_values(record)) for record in prediction_records
        )
        inventory[kind] = _prf(len(prediction_records), len(truth_records), len(pairs))
        matched_truth = {left for left, _, _, _ in pairs}
        matched_prediction = {right for _, right, _, _ in pairs}
        unmatched_truth.extend(
            _record_key(kind, record)
            for index, record in enumerate(truth_records)
            if index not in matched_truth
        )
        unmatched_prediction.extend(
            _record_key(kind, record)
            for index, record in enumerate(prediction_records)
            if index not in matched_prediction
        )
        for left, right, similarity, ambiguous in pairs:
            truth_record = truth_records[left]
            predicted_record = prediction_records[right]
            if ambiguous:
                ambiguous_truth.add(_record_key(kind, truth_record))
                ambiguous_prediction.add(_record_key(kind, predicted_record))
            matched_records.append((kind, truth_record, predicted_record))
            identity_pairs.append(
                (
                    kind,
                    _record_id(kind, truth_record),
                    _record_id(kind, predicted_record),
                )
            )
            matches.append(
                RecordMatch(
                    kind=kind,
                    truth_id=_record_id(kind, truth_record),
                    predicted_id=_record_id(kind, predicted_record),
                    similarity=similarity,
                    ambiguous=ambiguous,
                    family_features=family_pair_evidence(truth_record, predicted_record)
                    if kind == "device_families"
                    else None,
                    identity_evidence={
                        "truth_sample_code": [sample_code(truth_record)]
                        if sample_code(truth_record)
                        else [],
                        "prediction_sample_code": [sample_code(predicted_record)]
                        if sample_code(predicted_record)
                        else [],
                        "truth_measurement_types": sorted(
                            {
                                kind
                                for kind, _ in measurement_uses[0].get(
                                    truth_record.device_id, ()
                                )
                            }
                        ),
                        "prediction_measurement_types": sorted(
                            {
                                kind
                                for kind, _ in measurement_uses[1].get(
                                    predicted_record.device_id, ()
                                )
                            }
                        ),
                        "truth_measurement_scans": sorted(
                            f"{kind}:{scan}"
                            for kind, scan in measurement_uses[0].get(
                                truth_record.device_id, ()
                            )
                            if scan is not None
                        ),
                        "prediction_measurement_scans": sorted(
                            f"{kind}:{scan}"
                            for kind, scan in measurement_uses[1].get(
                                predicted_record.device_id, ()
                            )
                            if scan is not None
                        ),
                    }
                    if kind == "individual_devices"
                    else None,
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
        interpretation="reviewed_scope"
        if benchmark and benchmark.coverage.complete()
        else "reference_agreement",
        study_schema_sha256=study_schema_sha256(),
        truth_content_sha256=truth_digest,
        prediction_content_sha256=prediction_digest,
        run_manifest_sha256=run_manifest_sha256,
        run_id=run_id,
        benchmark=benchmark,
        prediction_validation=prediction_validation,
        run_efficiency=run_efficiency,
        input_warnings=_input_warnings(
            truth, prediction, benchmark, run_manifest_sha256
        ),
        config=config,
        core_facts=_core_fact_score(
            truth,
            prediction,
            identity_pairs,
            config,
            ambiguous_truth,
            ambiguous_prediction,
            _explanation,
        ),
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


def _input_warnings(
    truth: StudyExtraction,
    prediction: StudyExtraction,
    benchmark: BenchmarkProvenance | None,
    run_manifest_sha256: str | None,
) -> list[str]:
    """Expose metadata conflicts without treating an extracted DOI as source identity."""

    warnings = []
    if benchmark is None or not benchmark.coverage.complete():
        warnings.append(
            "Reference coverage/context is not fully reviewed. Counts describe agreement with supplied annotations; precision/F1 are diagnostic, and recall is limited to the annotated subset."
        )
    if run_manifest_sha256 is None:
        warnings.append(
            "Prediction has no sealed source provenance; source compatibility is unverified."
        )
    if benchmark is None or not benchmark.source_sha256:
        warnings.append(
            "Reference source provenance is unavailable; scores are diagnostic agreement."
        )

    def doi(value: str) -> str:
        return re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I
        ).casefold()

    if (
        truth.paper.doi
        and prediction.paper.doi
        and doi(truth.paper.doi) != doi(prediction.paper.doi)
    ):
        warnings.append(
            "Extracted paper DOI differs from the reference; check the bound source files."
        )
    return warnings


def evaluate_study_with_explanations(
    truth: StudyExtraction,
    prediction: StudyExtraction,
    *,
    facts: Sequence[FactSelection],
    max_candidates: int = 5,
    config: EvaluationConfig | None = None,
    benchmark: BenchmarkProvenance | None = None,
    prediction_validation: PredictionValidation | None = None,
    run_efficiency: RunEfficiency | None = None,
    run_id: str | None = None,
    run_manifest_sha256: str | None = None,
) -> tuple[EvaluationReport, ScoreExplanation]:
    """Evaluate once and explain selected facts from that very run's decisions.

    Select at most 25 scored JSON paths. The separate explanation artifact does
    not change the score report, its version, or its two independent match views.
    """

    collector = ExplanationCollector(facts, max_candidates)
    report = evaluate_study(
        truth,
        prediction,
        benchmark=benchmark,
        prediction_validation=prediction_validation,
        run_efficiency=run_efficiency,
        run_id=run_id,
        run_manifest_sha256=run_manifest_sha256,
        config=config,
        _explanation=collector,
    )
    return report, collector.finish(report)


def _study_digest(study: StudyExtraction) -> str:
    """Bind diagnostic paths to the exact parsed input, including IDs and list order."""

    content = json.dumps(
        study.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _metric_summary(
    values: Iterable[float | None], *, bootstrap_samples: int, seed: int
) -> MetricSummary:
    """Calculate a mean and paper-resampling interval without numerical dependencies."""

    defined = [value for value in values if value is not None]
    if not defined:
        return MetricSummary(
            paper_count=0,
            mean=None,
            ci95_lower=None,
            ci95_upper=None,
            interval_status="no_values",
        )
    mean = sum(defined) / len(defined)
    if len(defined) == 1 or bootstrap_samples == 0:
        return MetricSummary(
            paper_count=len(defined),
            mean=mean,
            ci95_lower=None,
            ci95_upper=None,
            interval_status="disabled"
            if bootstrap_samples == 0
            else "insufficient_papers",
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
        interval_status="available",
    )


def aggregate_evaluations(
    reports: Sequence[EvaluationReport],
    *,
    bootstrap_samples: int = 2_000,
    seed: int = 0,
    reference_release: ReferenceRelease | None = None,
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
    if reference_release is not None:
        reference_release.verify(provenance)
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
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=seed,
        paper_count=len(reports),
        split=split,
        paper_ids=paper_ids,
        reference_release_sha256=reference_release.digest()
        if reference_release
        else None,
        input_reports=[
            EvaluationInput(
                benchmark=r.benchmark,
                truth_content_sha256=r.truth_content_sha256,
                prediction_content_sha256=r.prediction_content_sha256,
                report_sha256=hashlib.sha256(
                    json.dumps(
                        r.model_dump(mode="json"),
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            )
            for r in reports
        ],
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
            cost_tracking_complete=all(
                r.run_efficiency is not None
                and r.run_efficiency.cost_tracking_complete is True
                for r in reports
            ),
            token_tracking_complete=all(
                r.run_efficiency is not None
                and r.run_efficiency.token_tracking_complete is True
                for r in reports
            ),
            cost_complete_papers=sum(
                r.run_efficiency is not None
                and r.run_efficiency.cost_tracking_complete is True
                for r in reports
            ),
            cost_incomplete_papers=sum(
                r.run_efficiency is not None
                and r.run_efficiency.cost_tracking_complete is False
                for r in reports
            ),
            cost_unknown_papers=sum(
                r.run_efficiency is None
                or r.run_efficiency.cost_tracking_complete is None
                for r in reports
            ),
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
        core_fact_groups_micro={
            group: _prf(
                sum(r.core_facts.groups[group].predicted for r in reports),
                sum(r.core_facts.groups[group].truth for r in reports),
                sum(r.core_facts.groups[group].matched for r in reports),
            )
            for group in FACT_GROUPS
        },
        core_fact_groups_macro_f1={
            group: _metric_summary(
                (r.core_facts.groups[group].f1 for r in reports),
                bootstrap_samples=bootstrap_samples,
                seed=seed + 200 + i,
            )
            for i, group in enumerate(FACT_GROUPS)
        },
        core_fact_subfields_micro={
            key: _prf(
                *(
                    sum(getattr(r.core_facts.subfields[key], field) for r in reports)
                    for field in ("predicted", "truth", "matched")
                )
            )
            for key in FACT_SUBFIELDS
        },
        core_fact_subfields_macro_f1={
            key: _metric_summary(
                (r.core_facts.subfields[key].f1 for r in reports),
                bootstrap_samples=bootstrap_samples,
                seed=seed + 100 + i,
            )
            for i, key in enumerate(FACT_SUBFIELDS)
        },
        core_fields_micro=_aggregate_fields(reports, "fields"),
        core_field_strata_micro=_aggregate_fields(reports, "strata"),
        core_facts_micro=_prf(
            sum(r.core_facts.micro.predicted for r in reports),
            sum(r.core_facts.micro.truth for r in reports),
            sum(r.core_facts.micro.matched for r in reports),
        ),
        core_facts_mean_paper_f1=_metric_summary(
            (r.core_facts.micro.f1 for r in reports),
            bootstrap_samples=bootstrap_samples,
            seed=seed + 300,
        ),
        core_recovery_micro=_prf(
            sum(r.core_facts.recovery.micro.predicted for r in reports),
            sum(r.core_facts.recovery.micro.truth for r in reports),
            sum(r.core_facts.recovery.micro.matched for r in reports),
        ),
        core_recovery_mean_paper_f1=_metric_summary(
            (r.core_facts.recovery.micro.f1 for r in reports),
            bootstrap_samples=bootstrap_samples,
            seed=seed + 301,
        ),
        papers_needing_scoring_review=sum(
            r.core_facts.scoring_status == "needs_review" for r in reports
        ),
        scoring_issue_count=sum(len(r.core_facts.issues) for r in reports),
        papers_with_unreviewed_scope=sum(
            r.interpretation != "reviewed_scope" for r in reports
        ),
    )


def _aggregate_fields(
    reports: Sequence[EvaluationReport], category: str
) -> dict[str, PRF]:
    """Sum counts before calculating rates; papers with no such field add zero."""
    keys = sorted({key for r in reports for key in getattr(r.core_facts, category)})
    return {
        key: _prf(
            *(
                sum(
                    getattr(
                        getattr(r.core_facts, category).get(key, _prf(0, 0, 0)), count
                    )
                    for r in reports
                )
                for count in ("predicted", "truth", "matched")
            )
        )
        for key in keys
    }
