"""Summarize actual comparison decisions without running another matcher.

Blockers are overlapping observations over same-concept candidates, not causal
explanations or probabilities. Context availability means that at least one
unambiguous candidate satisfies the context, regardless of its outcome value.
"""

from collections import Counter
from typing import Literal, cast

from .models import ReportedValue, StrictModel
from .quantities import LiteralClaim, ScalarClaim, interpret_quantity
from .scoring_facts import FactComparison, ScientificFact
from .scoring_protocol import protocol_name
from .scoring_requirements import requirements_for

Blocker = Literal[
    "no_candidate",
    "wrong_value",
    "missing_owner",
    "ambiguous_owner",
    "missing_defining_context",
    "conflicting_context",
    "one_to_one_competition",
]
UnitSource = Literal[
    "reported",
    "raw_value",
    "field_name",
    "percentage_points",
    "missing",
    "unsupported",
    "unparsed",
    "not_quantity",
]


class FactDiagnostic(StrictModel):
    # Optional for older saved diagnostic payloads; new reports always name the field.
    field: str | None = None
    path: str
    rule_id: str
    statistical_level: str | None
    value_interpretation: Literal["quantity", "literal", "nonquantity"]
    unit_source: UnitSource
    unsupported_condition_name: bool
    candidate_count: int
    owner_paired: bool
    context_available: bool
    eligible_candidates: int
    matched: bool
    blockers: list[Blocker]
    quantity_status: Literal["normalized", "literal", "contradiction"] | None = None
    normalization_reason: str | None = None


class DiagnosticCoverage(StrictModel):
    total: int
    owner_paired: int
    context_available: int
    literal_values: int
    unsupported_condition_names: int
    # Counts overlap: a fact can encounter several different candidate failures.
    blocker_counts: dict[str, int]


class ScoringDiagnostics(StrictModel):
    facts: dict[Literal["truth", "prediction"], list[FactDiagnostic]]
    coverage: dict[Literal["truth", "prediction"], DiagnosticCoverage]
    source_anchor_status: Literal["unavailable"] = "unavailable"


def diagnose_fact(
    fact: ScientificFact,
    decisions: list[FactComparison],
    matched: bool,
    *,
    ambiguous: bool,
) -> FactDiagnostic:
    """Describe eligibility even when value disagreement prevents final credit."""
    blockers: set[Blocker] = set()
    eligible = sum(d.eligible("attributed") for d in decisions)
    # Prefer candidates with the same paired owner when any exist. Other owners
    # are recovery candidates; they cannot explain a local context omission.
    candidates = [d for d in decisions if d.owner_equal] or decisions
    if not decisions:
        blockers.add("no_candidate")
    if fact.owner.unmatched_side is not None or (
        decisions and not any(d.owner_equal for d in decisions)
    ):
        blockers.add("missing_owner")
    if ambiguous:
        blockers.add("ambiguous_owner")
    for d in candidates:
        if d.ambiguity_blocked:
            blockers.add("ambiguous_owner")
        if not d.value_equal:
            blockers.add("wrong_value")
        if d.missing_context:
            blockers.add("missing_defining_context")
        if d.conflicting_context:
            blockers.add("conflicting_context")
    if eligible and not matched:
        blockers.add("one_to_one_competition")
    context = {c.name: c.value for c in fact.context.components}
    level = context.get("statistical_level")
    interpretation: Literal["quantity", "literal", "nonquantity"] = "nonquantity"
    unit_source: UnitSource = "not_quantity"
    quantity_status: Literal["normalized", "literal", "contradiction"] | None = None
    normalization_reason: str | None = None
    if isinstance(fact.value, ReportedValue):
        result = interpret_quantity(fact.value)
        claim = result.claim
        quantity_status, normalization_reason = result.status, result.reason
        interpretation = "quantity" if result.status == "normalized" else "literal"
        if result.status == "literal":
            unit_source = (
                "unsupported"
                if fact.value.unit and result.reason == "unsupported_unit"
                else "unparsed"
            )
        elif isinstance(claim, LiteralClaim) and result.unit_source not in {
            "reported",
            "raw_value",
            "field_name",
        }:
            unit_source = "unparsed"
        elif result.unit_source:
            unit_source = cast(UnitSource, fact.unit_source or result.unit_source)
        elif claim.unit is None:
            unit_source = "missing"
        elif isinstance(claim, ScalarClaim) and claim.canonical is None:
            unit_source = "unsupported"
        else:
            unit_source = fact.unit_source or (
                "reported" if fact.value.unit is not None else "raw_value"
            )
    return FactDiagnostic(
        field=fact.field_key,
        path=fact.path,
        rule_id=requirements_for(fact.concept, fact.context).rule_id,
        statistical_level=level if isinstance(level, str) else None,
        value_interpretation=interpretation,
        unit_source=unit_source,
        quantity_status=quantity_status,
        normalization_reason=normalization_reason,
        unsupported_condition_name=(
            fact.subfield == "measurement_protocol" and protocol_name(fact.name) is None
        ),
        candidate_count=len(decisions),
        owner_paired=any(d.owner_equal and not d.ambiguity_blocked for d in decisions),
        context_available=any(
            d.context_equal and not d.ambiguity_blocked for d in decisions
        ),
        eligible_candidates=eligible,
        matched=matched,
        blockers=[] if matched else sorted(blockers),
    )


def summarize_diagnostics(
    facts: dict[Literal["truth", "prediction"], list[FactDiagnostic]],
) -> ScoringDiagnostics:
    return ScoringDiagnostics(
        facts=facts,
        coverage={
            side: DiagnosticCoverage(
                total=len(rows),
                owner_paired=sum(r.owner_paired for r in rows),
                context_available=sum(r.context_available for r in rows),
                literal_values=sum(r.value_interpretation == "literal" for r in rows),
                unsupported_condition_names=sum(
                    r.unsupported_condition_name for r in rows
                ),
                blocker_counts=dict(Counter(b for r in rows for b in r.blockers)),
            )
            for side, rows in facts.items()
        },
    )
