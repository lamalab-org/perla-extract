"""Bounded explanations captured from the scorer's actual fact decisions.

This module never matches records or compares values. Its collector consumes the
same comparison matrix and selected assignments that produce the score ledger.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Literal, Sequence

from pydantic import Field

from .models import ReportedValue, StrictModel
from .quantities import normalize_claim
from .scoring_facts import FactComparison, FactGroup, ScientificFact
from .scoring_fields import FactSubfield
from .scoring_requirements import requirements_for

if TYPE_CHECKING:
    from .evaluation import EvaluationReport, FactMatch

ScoreView = Literal["attributed", "recovery"]
Side = Literal["truth", "prediction"]


class FactSelection(StrictModel):
    """Select one scored JSON field, rather than dumping every candidate pair."""

    side: Side
    path: str = Field(pattern=r"^/")
    view: ScoreView = "attributed"


class ContextMismatch(StrictModel):
    component: str
    reason: str
    truth_path: str | None
    predicted_path: str | None
    truth_value: object
    predicted_value: object


class AmbiguitySource(StrictModel):
    side: Side
    path: str


class CandidateExplanation(StrictModel):
    truth_path: str
    predicted_path: str
    value_equal: bool
    context_equal: bool
    context_status: Literal["equivalent", "contradictory", "unresolved"]
    ambiguity_blocked: bool
    eligible: bool
    selected: bool
    truth_claim: object
    predicted_claim: object
    context_mismatches: list[ContextMismatch]
    ambiguity_sources: list[AmbiguitySource]
    ambiguity_sources_truncated: bool
    # A candidate can be eligible but consumed by another fact's assignment.
    competing_truth_path: str | None = None
    competing_predicted_path: str | None = None


class NearbyProperty(StrictModel):
    path: str
    group: FactGroup
    name: str


class FactExplanation(StrictModel):
    selection: FactSelection
    canonical_path: str
    source_paths: list[str]
    group: FactGroup
    subfield: FactSubfield
    name: str
    rule_id: str
    identity_fields: list[str]
    defining_fields: list[str]
    independent_fields: list[str]
    owner: str
    owner_matched: bool
    reason: Literal[
        "matched",
        "owner_unmatched",
        "property_mismatch",
        "no_candidate",
        "ambiguity",
        "value_mismatch",
        "context_mismatch",
        "value_and_context_mismatch",
        "one_to_one_competition",
    ]
    candidate_count: int
    eligible_candidate_count: int
    candidates_truncated: bool
    candidates: list[CandidateExplanation]
    ambiguity_sources: list[AmbiguitySource]
    ambiguity_sources_truncated: bool
    nearby_properties: list[NearbyProperty] = Field(default_factory=list)
    nearby_properties_truncated: bool = False


class ScoreExplanation(StrictModel):
    """Explain selected canonical facts and source aliases from one evaluation."""

    format_version: Literal[6] = 6
    evaluation_format_version: int
    matcher_version: str
    fact_profile: str
    truth_content_sha256: str
    prediction_content_sha256: str
    config: dict[str, object]
    max_candidates: int
    facts: list[FactExplanation]


def _describe(value: object, depth: int = 0) -> object:
    """Bound diagnostic values as well as candidate counts; paths retain the source."""

    if depth > 6:
        return "[nested value omitted]"
    if isinstance(value, ReportedValue):
        claim = normalize_claim(value)
        fields = _describe(asdict(claim), depth + 1)
        assert isinstance(fields, dict)
        return {"kind": type(claim).__name__, **fields}
    if is_dataclass(value) and not isinstance(value, type):
        return _describe(asdict(value), depth + 1)
    if isinstance(value, dict):
        items = list(value.items())
        result = {str(k): _describe(v, depth + 1) for k, v in items[:20]}
        if len(items) > 20:
            result["omitted_entries"] = len(items) - 20
        return result
    if isinstance(value, (tuple, list)):
        entries = [_describe(v, depth + 1) for v in value[:8]]
        if len(value) > 8:
            entries.append({"omitted_entries": len(value) - 8})
        return entries
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "… [truncated]"
    return value


class ExplanationCollector:
    """Internal sink whose only inputs are the live scorer's decisions."""

    def __init__(self, facts: Sequence[FactSelection], max_candidates: int):
        if not 1 <= len(facts) <= 25:
            raise ValueError("Explain between 1 and 25 scored fact paths")
        if not 1 <= max_candidates <= 20:
            raise ValueError("max_candidates must be between 1 and 20")
        self.selections = [FactSelection.model_validate(fact) for fact in facts]
        self.requested_selections = list(self.selections)
        keys = [(f.side, f.path, f.view) for f in self.selections]
        if len(set(keys)) != len(keys):
            raise ValueError("Fact selections must be distinct")
        self.max_candidates = max_candidates
        self.selected_truth = {s.path for s in self.selections if s.side == "truth"}
        self.selected_prediction = {
            s.path for s in self.selections if s.side == "prediction"
        }
        self.facts: tuple[list[ScientificFact], list[ScientificFact]] = ([], [])
        self.blocked: tuple[set[str], set[str]] = (set(), set())
        self.results: dict[tuple[str, str, str], FactExplanation] = {}
        self.ambiguity_sources: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def selects_pair(self, truth_path: str, predicted_path: str) -> bool:
        return (
            truth_path in self.selected_truth
            or predicted_path in self.selected_prediction
        )

    def start(
        self,
        truth: list[ScientificFact],
        prediction: list[ScientificFact],
        blocked: tuple[set[str], set[str]],
        ambiguity_sources: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> None:
        self.facts = truth, prediction
        self.blocked = blocked
        self.ambiguity_sources = ambiguity_sources
        paths = [
            {path: fact.path for fact in side for path in fact.source_paths}
            for side in self.facts
        ]
        for selection in self.selections:
            if selection.path not in paths[0 if selection.side == "truth" else 1]:
                raise ValueError(
                    f"Not a scored {selection.side} fact path: {selection.path}"
                )
        self.selections = [
            s.model_copy(update={"path": paths[0 if s.side == "truth" else 1][s.path]})
            for s in self.requested_selections
        ]
        self.selected_truth = {s.path for s in self.selections if s.side == "truth"}
        self.selected_prediction = {
            s.path for s in self.selections if s.side == "prediction"
        }

    def capture_bucket(
        self,
        left: list[ScientificFact],
        right: list[ScientificFact],
        decisions: list[list[FactComparison]],
        matches: dict[str, list[FactMatch]],
    ) -> None:
        for selection in self.selections:
            side = 0 if selection.side == "truth" else 1
            own, other = (left, right) if side == 0 else (right, left)
            index = next(
                (i for i, fact in enumerate(own) if fact.path == selection.path), None
            )
            if index is None:
                continue
            fact = own[index]
            selected = matches[selection.view]
            selected_pairs = {(m.truth_path, m.predicted_path) for m in selected}
            by_truth = {m.truth_path: m.predicted_path for m in selected}
            by_prediction = {m.predicted_path: m.truth_path for m in selected}
            candidates = []
            for j, candidate in enumerate(other):
                a, b = (fact, candidate) if side == 0 else (candidate, fact)
                decision = decisions[index][j] if side == 0 else decisions[j][index]
                chosen = (a.path, b.path) in selected_pairs
                candidates.append((a, b, decision, chosen))
            eligible = sum(d.eligible(selection.view) for _, _, d, _ in candidates)
            if any(chosen for _, _, _, chosen in candidates):
                reason = "matched"
            elif selection.view != "recovery" and fact.owner.unmatched_side is not None:
                reason = "owner_unmatched"
            elif eligible:
                reason = "one_to_one_competition"
            elif selection.view != "recovery" and (
                fact.path in self.blocked[side]
                or (
                    candidates and all(d.ambiguity_blocked for _, _, d, _ in candidates)
                )
                or any(
                    d.ambiguity_blocked and d.context_equal and d.value_equal
                    for _, _, d, _ in candidates
                )
            ):
                reason = "ambiguity"
            elif not candidates:
                reason = (
                    "property_mismatch"
                    if any(f.owner == fact.owner for f in self.facts[1 - side])
                    else "no_candidate"
                )
            elif selection.view == "recovery":
                reason = "value_mismatch"
            elif any(d.value_equal for _, _, d, _ in candidates):
                reason = "context_mismatch"
            elif any(
                d.context_equal and not d.ambiguity_blocked for _, _, d, _ in candidates
            ):
                reason = "value_mismatch"
            else:
                reason = "value_and_context_mismatch"
            # Show the selected pair first, then eligible alternatives, then the
            # closest decisions. This ranks explanations, never scoring candidates.
            candidates.sort(
                key=lambda row: (
                    not row[3],
                    not row[2].eligible(selection.view),
                    -(int(row[2].value_equal) + int(row[2].context_equal)),
                    row[0].path,
                    row[1].path,
                )
            )
            shown = []
            for a, b, decision, chosen in candidates[: self.max_candidates]:
                ambiguity_paths = sorted(
                    set(
                        self.ambiguity_sources.get(("truth", a.path), [])
                        + self.ambiguity_sources.get(("prediction", b.path), [])
                    )
                )
                shown.append(
                    CandidateExplanation(
                        truth_path=a.path,
                        predicted_path=b.path,
                        value_equal=decision.value_equal,
                        context_equal=decision.context_equal,
                        context_status=decision.context_status,
                        ambiguity_blocked=decision.ambiguity_blocked,
                        eligible=decision.eligible(selection.view),
                        selected=chosen,
                        truth_claim=_describe(a.value),
                        predicted_claim=_describe(b.value),
                        context_mismatches=[
                            ContextMismatch(
                                component=d.name,
                                reason=d.reason,
                                truth_path=d.truth_path,
                                predicted_path=d.predicted_path,
                                truth_value=_describe(d.truth_value),
                                predicted_value=_describe(d.predicted_value),
                            )
                            for d in decision.context_differences
                        ],
                        ambiguity_sources=[
                            AmbiguitySource(side=s, path=p)
                            for s, p in ambiguity_paths[:20]
                        ],
                        ambiguity_sources_truncated=len(ambiguity_paths) > 20,
                        competing_truth_path=(
                            by_prediction.get(b.path)
                            if by_prediction.get(b.path) not in (None, a.path)
                            else None
                        ),
                        competing_predicted_path=(
                            by_truth.get(a.path)
                            if by_truth.get(a.path) not in (None, b.path)
                            else None
                        ),
                    )
                )
            nearby = (
                [
                    f
                    for f in self.facts[1 - side]
                    if f.owner == fact.owner
                    and (f.group, f.name) != (fact.group, fact.name)
                ]
                if not candidates
                else []
            )
            # Keep relevant alternative property names visible when a family has
            # many unrelated facts. This orders diagnostics, never match candidates.
            nearby.sort(
                key=lambda f: (
                    f.subfield != fact.subfield,
                    f.group != fact.group,
                    f.path,
                )
            )
            self.results[(selection.side, selection.path, selection.view)] = (
                FactExplanation(
                    selection=selection,
                    canonical_path=fact.path,
                    source_paths=list(fact.source_paths),
                    group=fact.group,
                    subfield=fact.subfield,
                    name=fact.name,
                    rule_id=requirements_for(fact.concept, fact.context).rule_id,
                    identity_fields=list(
                        requirements_for(fact.concept, fact.context).identity_fields
                    ),
                    defining_fields=list(
                        requirements_for(fact.concept, fact.context).defining_fields
                    ),
                    independent_fields=list(
                        requirements_for(fact.concept, fact.context).independent_fields
                    ),
                    owner=fact.owner.key,
                    owner_matched=fact.owner.unmatched_side is None,
                    reason=reason,
                    candidate_count=len(candidates),
                    eligible_candidate_count=eligible,
                    candidates_truncated=len(candidates) > self.max_candidates,
                    candidates=shown,
                    ambiguity_sources=[
                        AmbiguitySource(side=s, path=p)
                        for s, p in self.ambiguity_sources.get(
                            (selection.side, selection.path), []
                        )[:20]
                    ],
                    ambiguity_sources_truncated=len(
                        self.ambiguity_sources.get((selection.side, selection.path), [])
                    )
                    > 20,
                    nearby_properties=[
                        NearbyProperty(path=f.path, group=f.group, name=f.name)
                        for f in nearby[: self.max_candidates]
                    ],
                    nearby_properties_truncated=len(nearby) > self.max_candidates,
                )
            )

    def finish(self, report: EvaluationReport) -> ScoreExplanation:
        return ScoreExplanation(
            evaluation_format_version=report.format_version,
            matcher_version=report.matcher_version,
            fact_profile=report.core_facts.profile,
            truth_content_sha256=report.truth_content_sha256,
            prediction_content_sha256=report.prediction_content_sha256,
            config=report.config.model_dump(mode="json"),
            max_candidates=self.max_candidates,
            facts=[
                self.results[(s.side, s.path, s.view)].model_copy(
                    update={"selection": requested}
                )
                for s, requested in zip(self.selections, self.requested_selections)
            ],
        )
