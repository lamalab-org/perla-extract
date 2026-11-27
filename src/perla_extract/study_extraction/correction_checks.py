"""Check correction integrity without treating a technical pass as scientific truth.

Source-grounded claims retain their measurement scope and multiplicity. Local IDs
anchor explicit edits; they never establish specimen identity across studies.
Ambiguous correspondence and unsupported semantic deletion keep the earlier draft.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .claims import ClaimLedger, audit_claim_coverage
from .models import (
    AbsorberComponent,
    EvidenceBlock,
    Layer,
    MaterialConstituent,
    PerformanceObservation,
    PopulationStatistic,
    ProcessingStep,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
    SubjectContext,
)
from .quantities import (
    CompoundClaim,
    LiteralClaim,
    ScalarClaim,
    interpret_quantity,
    scientific_text,
    scientific_value_text,
)
from .validation import scientific_record_fingerprint, validate_study

RECORD_COLLECTIONS = {
    "device_family": ("device_families", "family_id"),
    "individual_device": ("individual_devices", "device_id"),
    "performance_observation": ("performance_observations", "observation_id"),
    "population_statistic": ("population_statistics", "population_id"),
    "stability_test": ("stability_tests", "test_id"),
}


def record_for_path(study: StudyExtraction, path: str) -> tuple[str, object] | None:
    """Resolve a validation JSON path to its top-level scientific record."""

    match = re.match(r"^\$\.([a-z_]+)\[(\d+)\]", path)
    if not match or not hasattr(study, match.group(1)):
        return None
    collection = getattr(study, match.group(1))
    index = int(match.group(2))
    if not isinstance(collection, list) or index >= len(collection):
        return None
    kind = next(
        (
            candidate
            for candidate, (name, _) in RECORD_COLLECTIONS.items()
            if name == match.group(1)
        ),
        match.group(1),
    )
    return kind, collection[index]


def candidate_quality(
    study: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
    *,
    baseline: StudyExtraction | None = None,
) -> dict[str, int]:
    """Measure technical issues and source-value loss relative to a prior draft.

    Reader notes may be incomplete. Removing a record can reduce their mismatch
    count while deleting real data, so source-supported values are checked against
    the prior draft independently. Exact repetitions share one key; adding another
    value cannot compensate for losing a different one.
    """

    validation = validate_study(study, blocks)
    uncovered = 0
    missing_claims = 0
    if ledger is not None:
        coverage = audit_claim_coverage(ledger, study)
        uncovered = int(coverage["issue_count"])
        missing_claims = sum(
            int(coverage["counts"][key])
            for key in ("unmatched", "possible_match", "missing_shared_targets")
        )
    retention = (
        _retention_changes(baseline, study, blocks, validation)
        if baseline is not None
        else {
            "lost_verified_values": 0,
            "ownership_changes": 0,
            "ambiguous_ownership_edits": 0,
            "context_enrichments": 0,
        }
    )
    return {
        "validation_issues": len(validation["issues"]),
        "reported_values": int(validation["counts"]["reported_values"]),
        "source_verified_values": int(validation["counts"]["source_verified_values"]),
        "semantic_issues": uncovered,
        "missing_claims": missing_claims,
        **retention,
    }


def _claim_key(value: ReportedValue) -> tuple:
    """Retain complete source meaning independently of convenience-field errors.

    A parsed-number contradiction must be repairable without treating the invalid
    number as a scientific value. Its intact raw claim remains protected. Units,
    bounds, approximation, chemical case and charges are never flattened away.
    """

    interpretation = interpret_quantity(value)
    if interpretation.status == "contradiction":
        interpretation = interpret_quantity(
            value.model_copy(update={"value_number": None, "unit": None})
        )
    claim = interpretation.claim
    name = interpretation.kind or scientific_text(value.name)
    if isinstance(claim, ScalarClaim):
        magnitude, unit = (
            (claim.canonical.magnitude, claim.canonical.unit)
            if claim.canonical is not None
            else (claim.number, claim.unit)
        )
        # Decimal keys discard floating-point unit-conversion noise, not the
        # benchmark's 1% tolerance. Distinct reported numbers remain distinct.
        magnitude_key = format(magnitude, ".15g") if magnitude else "0"
        return name, "scalar", magnitude_key, unit, claim.qualifier
    if isinstance(claim, CompoundClaim):
        return (
            name,
            claim.kind,
            format(claim.first, ".15g") if claim.first else "0",
            format(claim.second, ".15g") if claim.second else "0",
            claim.unit,
        )
    assert isinstance(claim, LiteralClaim)
    return (
        name,
        "literal",
        scientific_value_text(value.raw_value),
        scientific_text(value.unit or ""),
    )


def _record_context(record: object) -> tuple:
    """Use reported measurement/statistical distinctions, not display labels."""

    fields = (
        ("measurement_type", "scan_direction")
        if isinstance(record, PerformanceObservation)
        else (
            "statistic_type",
            "sample_size",
            "error_bar_type",
            "measurement_type",
            "scan_direction",
        )
        if isinstance(record, PopulationStatistic)
        else ()
    )
    return tuple((name, getattr(record, name)) for name in fields)


def _subject_context(subject: SubjectContext) -> tuple:
    """Identify a treatment arm by its complete, order-independent conditions."""

    return (
        ("treatment_stage", subject.treatment_stage),
        ("treated_material", scientific_text(subject.treated_material or "")),
        (
            "subject_conditions",
            tuple(
                sorted({_claim_key(value) for value in subject.conditions}, key=repr)
            ),
        ),
    )


def _nested_scope(study: StudyExtraction, path: str) -> tuple[tuple, tuple, tuple]:
    """Locate a value using schema fields; IDs only anchor an explicit edit scope."""

    node: object = study
    roles: list[str] = []
    owners: list[tuple[str, str]] = []
    context: list[tuple] = []
    for field, index in re.findall(r"\.([a-z_]+)|\[(\d+)\]", path):
        if field:
            node = getattr(node, field)
            roles.append(field)
        elif isinstance(node, list):
            node = node[int(index)]
        else:
            raise ValueError("a validation value path has an invalid list position")
        if isinstance(node, ProcessingStep):
            owners.append(("step_id", node.step_id))
            context.extend(
                (
                    ("operation", scientific_text(node.operation)),
                    ("sequence", node.sequence),
                )
            )
        elif isinstance(node, Layer):
            owners.append(("layer_id", node.layer_id))
            context.extend(
                (
                    ("layer_material", scientific_text(node.material)),
                    ("sequence", node.sequence),
                    ("role", node.role),
                )
            )
        elif isinstance(node, AbsorberComponent):
            owners.append(("absorber_id", node.absorber_id))
            if node.layer_id is not None:
                owners.append(("layer_id", node.layer_id))
        elif isinstance(node, StabilityCheckpoint):
            owners.append(("checkpoint_id", node.checkpoint_id))
            if node.time is not None:
                context.append(("checkpoint_time", _claim_key(node.time)))
        elif isinstance(node, SubjectContext):
            scope = _subject_context(node)
            # A device has one treatment context, so additional conditions
            # cannot collapse independently reported arms within that device.
            context.extend(scope if roles[0] == "population_statistics" else scope[:2])
        elif isinstance(node, MaterialConstituent):
            context.append(("constituent", scientific_text(node.name), node.role))
    return tuple(roles[1:]), tuple(owners), tuple(context)


def _verified_claim_inventory(
    study: StudyExtraction, validation: dict[str, Any]
) -> dict[tuple, Counter[tuple]]:
    """Count source claims by meaning and explicit owner, retaining multiplicity.

    Identical entries within one scope count once. Distinct measurements and
    specimens count separately. IDs establish edit anchors only; moving one
    uniquely corresponding claim is reported separately from deleting it.
    """

    inventory: dict[tuple, Counter[tuple]] = defaultdict(Counter)
    seen: set[tuple] = set()
    duplicate_records: dict[tuple[str, str], str] = {}
    duplicate_checkpoints: dict[tuple[str, str], str] = {}
    subject_counts = {
        record.population_id: Counter(_subject_context(s) for s in record.subjects)
        for record in study.population_statistics
    }
    for kind in ("performance_observation", "population_statistic", "stability_test"):
        collection, identifier = RECORD_COLLECTIONS[kind]
        canonical: dict[str, str] = {}
        checkpoint_anchors: dict[str, list[str]] = {}
        ignored = {identifier, "evidence"}
        if kind == "stability_test":
            ignored.add("checkpoint_id")
        for record in getattr(study, collection):
            signature = scientific_record_fingerprint(record, ignored)
            record_id = str(getattr(record, identifier))
            duplicate_records[(kind, record_id)] = canonical.setdefault(
                signature, record_id
            )
            if isinstance(record, StabilityTest):
                anchors = checkpoint_anchors.setdefault(
                    signature, [point.checkpoint_id for point in record.checkpoints]
                )
                duplicate_checkpoints.update(
                    ((record_id, point.checkpoint_id), anchor)
                    for point, anchor in zip(record.checkpoints, anchors)
                )
    for value in validation["verified_values"]:
        resolved = record_for_path(study, str(value["path"]))
        if resolved is None:
            continue
        kind, record = resolved
        _, identifier = RECORD_COLLECTIONS[kind]
        record_id = str(getattr(record, identifier))
        original_id = record_id
        record_id = duplicate_records.get((kind, record_id), record_id)
        roles, nested_owners, context = _nested_scope(study, str(value["path"]))
        nested_owners = tuple(
            (name, duplicate_checkpoints.get((original_id, anchor), anchor))
            if name == "checkpoint_id"
            else (name, anchor)
            for name, anchor in nested_owners
        )
        claim = ReportedValue.model_validate(
            {
                key: value[key]
                for key in ("name", "raw_value", "value_number", "unit", "evidence")
            }
        )
        key = kind, roles, _record_context(record), context, _claim_key(claim)
        owner = (
            record_id,
            getattr(record, "device_id", None),
            getattr(record, "family_id", None),
            nested_owners,
        )
        occurrence = key, owner
        if occurrence not in seen:
            # Repeating a condition inside one arm is redundant; reporting that
            # condition in two arms retains two scoped claims, even if arms agree.
            multiplicity = (
                subject_counts[original_id][context]
                if kind == "population_statistic" and roles[:1] == ("subjects",)
                else 1
            )
            inventory[key][owner] += multiplicity
            seen.add(occurrence)
    return dict(inventory)


def _subject_extensions(
    baseline: StudyExtraction, candidate: StudyExtraction
) -> set[tuple]:
    """Allow arm enrichment only with unique correspondence in both directions.

    Per-value matching alone could attach disjoint arms to one enlarged arm. The
    complete arm relation must be unambiguous before any of its claims can move.
    Multiplicity is checked separately by the source-claim inventory.
    """

    allowed: set[tuple] = set()
    candidates = {p.population_id: p for p in candidate.population_statistics}
    for record in baseline.population_statistics:
        revised = candidates.get(record.population_id)
        if revised is None:
            continue
        prior = {_subject_context(s) for s in record.subjects}
        current = {_subject_context(s) for s in revised.subjects}
        matches = {
            old: [new for new in current if _context_extends(old, new)] for old in prior
        }
        incoming = Counter(new for targets in matches.values() for new in targets)
        allowed.update(
            (record.population_id, old, targets[0])
            for old, targets in matches.items()
            if len(targets) == 1 and incoming[targets[0]] == 1
        )
    return allowed


def _retention_changes(
    baseline: StudyExtraction,
    candidate: StudyExtraction,
    blocks: list[EvidenceBlock],
    validation: dict[str, Any],
) -> dict[str, int]:
    before = _verified_claim_inventory(baseline, validate_study(baseline, blocks))
    after = _verified_claim_inventory(candidate, validation)
    subject_extensions = _subject_extensions(baseline, candidate)
    lost = moved = ambiguous = enriched = 0
    for key, owners in before.items():
        remaining = after.get(key, Counter()).copy()
        subject_scoped = key[0] == "population_statistic" and key[1][:1] == (
            "subjects",
        )
        # A uniquely anchored claim can acquire previously missing context. An
        # explicit conflicting method/direction remains a different assertion.
        for owner, count in (owners - remaining).items():
            matches = [
                (other, target)
                for other, targets in after.items()
                if other != key
                and other[:2] == key[:2]
                and other[4] == key[4]
                and _context_extends(key[2], other[2])
                and _context_extends(key[3], other[3])
                and (
                    not subject_scoped
                    or (owner[0], key[3], other[3]) in subject_extensions
                )
                for target in targets
                if targets[target] > 0
                and owner[0] == target[0]
                and owner[3] == target[3]
            ]
            if len(matches) == 1:
                other, target = matches[0]
                retained = min(count, after[other][target])
                remaining[target] += retained
                after[other][target] -= retained
                enriched += retained
        removed, added = owners - remaining, remaining - owners
        # Existing record IDs anchor an explicit relationship edit. They are not
        # evidence that two independently generated scientific subjects agree.
        for old in list(removed):
            matches = [new for new in added if old[0] == new[0] and old[3] == new[3]]
            if removed[old] and len(matches) == 1:
                new = matches[0]
                count = min(removed[old], added[new])
                removed[old] -= count
                added[new] -= count
                moved += count
        removed += Counter()
        added += Counter()
        old_count, new_count = sum(removed.values()), sum(added.values())
        lost += max(old_count - new_count, 0)
        if old_count and new_count:
            if old_count == new_count == 1:
                moved += 1
            else:
                ambiguous += min(old_count, new_count)
    return {
        "lost_verified_values": lost,
        "ownership_changes": moved,
        "ambiguous_ownership_edits": ambiguous,
        "context_enrichments": enriched,
    }


def _context_extends(before: tuple, after: tuple) -> bool:
    """Allow additional context while retaining every explicitly reported value."""

    current = {entry[0]: entry[1:] for entry in after}
    for entry in before:
        prior = entry[1:]
        values = current.get(entry[0])
        if values is None or len(prior) != len(values):
            return False
        if entry[0] == "subject_conditions":
            if not set(prior[0]) <= set(values[0]):
                return False
            continue
        if any(
            a not in (None, "", "not_reported") and a != b
            for a, b in zip(prior, values)
        ):
            return False
    return True


def is_monotonic_quality(before: dict[str, int], after: dict[str, int]) -> bool:
    """Return whether a candidate avoids trading away any grounded signal."""

    return (
        after["validation_issues"] <= before["validation_issues"]
        and after["semantic_issues"] <= before["semantic_issues"]
        and after["missing_claims"] <= before["missing_claims"]
        and after["lost_verified_values"] == 0
        and after.get("ambiguous_ownership_edits", 0) == 0
    )


def _select_refinement_candidate(
    draft: StudyExtraction,
    refinement: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
) -> tuple[StudyExtraction, dict[str, object]]:
    """Keep source-supported values while checking source links and note coverage.

    These source-link and claim-coverage counts are integrity checks, not a measure
    of scientific correctness. Tied issue counts permit a changed candidate only
    when source values survive; measure accuracy against reviewed references.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    refinement_quality = candidate_quality(refinement, blocks, ledger, baseline=draft)
    same_candidate = draft == refinement
    selected = (
        "refinement"
        if not same_candidate
        and is_monotonic_quality(draft_quality, refinement_quality)
        else "draft"
    )
    reason = (
        "refinement preserved source-supported values without worsening "
        "validation or reader-note coverage"
    )
    if same_candidate:
        reason = "refinement produced no candidate change"
    elif selected == "draft":
        reason = (
            "refinement lost source-supported values or worsened validation "
            "or reader-note coverage"
        )
    return (
        draft if selected == "draft" else refinement,
        {
            "selected": selected,
            "reason": reason,
            "proposal_decision": {"selected": selected, "reason": reason},
            "draft_quality": draft_quality,
            "refinement_quality": refinement_quality,
        },
    )


def _gate_final_candidate_against_draft(
    draft: StudyExtraction,
    candidate: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
    selection: dict[str, object],
) -> tuple[StudyExtraction, dict[str, object]]:
    """Compare the final corrected candidate with the original draft once more.

    Repair is checked against its immediate input. This final comparison prevents
    a chain of accepted edits from losing source values or worsening integrity
    relative to the first valid draft. Record counts are not rewards: removing an
    exact duplicate can be a legitimate correction.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    candidate_quality_summary = candidate_quality(
        candidate, blocks, ledger, baseline=draft
    )
    accepted = is_monotonic_quality(draft_quality, candidate_quality_summary)
    result = dict(selection)
    result.setdefault(
        "proposal_decision",
        {"selected": selection["selected"], "reason": selection.get("reason")},
    )
    result["pre_repair_selected"] = selection["selected"]
    result["draft_quality"] = draft_quality
    result["final_candidate_quality"] = candidate_quality_summary
    integrity_reason = (
        "final candidate preserves source-supported claims without worsening integrity"
        if accepted
        else "final candidate loses source-supported claims or worsens integrity"
    )
    result["final_integrity_check"] = {"accepted": accepted, "reason": integrity_reason}
    if accepted:
        result["selected"] = str(selection["selected"])
        # A retained draft passing this check does not make the rejected proposal
        # acceptable. Preserve that selection explanation at the top level too.
        result["reason"] = selection.get("reason", integrity_reason)
        return candidate, result
    result["selected"] = "draft"
    result["reason"] = (
        "final candidate lost source-supported values or worsened validation "
        "or reader-note coverage relative to the draft"
    )
    return draft, result
