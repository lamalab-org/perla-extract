"""Add reviewable interpretations without changing reported source records.

The rich extraction deliberately preserves source language.  Downstream schemas need
some semantic decisions that are not verbatim facts, such as assigning formula terms
to crystallographic sites or identifying which process condition is a duration.  This
module isolates those decisions in small model calls and accepts them only when local,
deterministic checks can connect them back to an existing atomic value.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import assembled_from_quotes, source_contains_text
from .models import (
    AbsorberComponent,
    DeviceFamily,
    EvidenceBlock,
    ProcessingStep,
    ReportedValue,
    StudyExtraction,
)
from .units import convert_reported_value, is_concentration_unit
from .vocabulary import NormalizedAtmosphere

if TYPE_CHECKING:
    from .client import ModelClient

ENRICHMENT_SYSTEM_PROMPT = """Interpret only the supplied extracted records and their
local source evidence. Never add a device, material, condition, number, or formula.
Return an empty proposal list when the evidence does not support a requested mapping."""

COMPOSITION_ENRICHMENT_PROMPT = """Assign the terms of each reported absorber
formula to its A, B, and X sites.

Rules:
- Copy ion abbreviations and stoichiometric coefficients from the reported formula.
- Use coefficient "1" only when a coefficient is implicit.
- Preserve the order of terms within each site.
- Do not infer a formula from a layer name or from general chemical knowledge.
- Do not repair or complete a malformed formula.
- Copy the supplied family_id and absorber_id into each proposal.
- Emit at most one proposal for each supplied absorber_id.
"""

PROCESSING_ENRICHMENT_PROMPT = """Classify existing atomic process values and
materials into the supplied normalized roles.

Rules:
- Refer only to the supplied condition_index and material_index values. Never output
  a new measured value.
- A condition may map to at most one target field. Map temperature and duration only
  when the indexed value reports that quantity.
- Set atmosphere to null for temperature and duration assignments.
- For atmosphere, select the closest allowed vocabulary value only when the source
  explicitly reports the atmosphere; otherwise omit the assignment.
- Classify each indexed material as solute, solvent, antisolvent, or other. Use other
  when its role is unclear.
- Link concentration_condition_index only to an explicitly reported concentration of
  that solute; otherwise set it to null.
- Equal concentration values attached to different named solutes are separate atomic
  conditions. Link each solute to its own condition index rather than treating the
  repeated number as a duplicate.
- Emit at most one proposal for each supplied step_id.
"""

ProposalStatus = Literal["accepted", "needs_review", "rejected"]


class _StrictModel(BaseModel):
    """Reject accidental fields so every model decision remains inspectable."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProposedIon(_StrictModel):
    """Represent one formula term assigned to a perovskite site."""

    site: Literal["A", "B", "X"]
    abbreviation: str = Field(min_length=1, max_length=120)
    coefficient: str = Field(min_length=1, max_length=80)


class CompositionProposal(_StrictModel):
    """Group all site assignments proposed for one scoped absorber."""

    family_id: str = Field(min_length=1, max_length=200)
    absorber_id: str | None = Field(default=None, min_length=1, max_length=200)
    ions: list[ProposedIon] = Field(max_length=40)


class CompositionProposalResponse(_StrictModel):
    """Bound the response from one batched composition interpretation call."""

    proposals: list[CompositionProposal] = Field(max_length=500)


class ProcessingConditionAssignment(_StrictModel):
    """Map one indexed source condition to one normalized process field."""

    condition_index: int = Field(ge=0)
    target_field: Literal["temperature", "duration", "atmosphere"]
    atmosphere: NormalizedAtmosphere | None


class ProcessingMaterialAssignment(_StrictModel):
    """Classify one indexed source material without rewriting its name."""

    material_index: int = Field(ge=0)
    role: Literal["solute", "solvent", "antisolvent", "other"]
    concentration_condition_index: int | None = Field(ge=0)


class ProcessingStepProposal(_StrictModel):
    """Collect normalized references for one existing processing step."""

    step_id: str = Field(min_length=1, max_length=200)
    condition_assignments: list[ProcessingConditionAssignment] = Field(max_length=50)
    material_assignments: list[ProcessingMaterialAssignment] = Field(max_length=30)


class ProcessingProposalResponse(_StrictModel):
    """Bound the response from one batched processing interpretation call."""

    proposals: list[ProcessingStepProposal] = Field(max_length=1000)


class CompositionProposalResult(_StrictModel):
    """Record whether deterministic formula reconstruction accepted a proposal."""

    proposal: CompositionProposal
    status: ProposalStatus
    issues: list[str] = Field(default_factory=list)


class ProcessingProposalResult(_StrictModel):
    """Record whether every proposed pointer resolves to an atomic source value."""

    proposal: ProcessingStepProposal
    status: ProposalStatus
    issues: list[str] = Field(default_factory=list)


class EnrichmentAudit(_StrictModel):
    """Keep all accepted and reviewable enrichment decisions beside extraction.json."""

    composition_results: list[CompositionProposalResult] = Field(default_factory=list)
    processing_results: list[ProcessingProposalResult] = Field(default_factory=list)
    unresolved_absorber_ids: list[str] = Field(default_factory=list)
    unresolved_processing_step_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_unscoped_ids(cls, value: object) -> object:
        """Read historical audits while exposing absorber-scoped output going forward."""

        if isinstance(value, dict) and "unresolved_composition_ids" in value:
            migrated = dict(value)
            migrated["unresolved_absorber_ids"] = migrated.pop(
                "unresolved_composition_ids"
            )
            return migrated
        return value


def _citations_for_absorber(
    family: DeviceFamily, absorber: AbsorberComponent
) -> Iterable[str]:
    """Collect evidence IDs attached to one absorber without mixing subcells."""

    for citation in absorber.evidence:
        yield citation.block_id
    if absorber.formula:
        for citation in absorber.formula.evidence:
            yield citation.block_id
    for constituent in absorber.constituents:
        for citation in constituent.evidence:
            yield citation.block_id
    for layer in family.layers:
        if layer.layer_id == absorber.layer_id:
            for citation in layer.evidence:
                yield citation.block_id


def _citations_for_step(step: ProcessingStep) -> Iterable[str]:
    """Collect only evidence IDs already attached to a process and its values."""

    for citation in step.evidence:
        yield citation.block_id
    for condition in step.conditions:
        for citation in condition.evidence:
            yield citation.block_id


def _local_evidence(
    citation_ids: Iterable[str], blocks: dict[str, EvidenceBlock]
) -> list[dict[str, object]]:
    """Resolve cited blocks once, preserving their document location for review."""

    resolved: list[dict[str, object]] = []
    for block_id in dict.fromkeys(citation_ids):
        block = blocks.get(block_id)
        if block is not None:
            resolved.append(
                {
                    "block_id": block.block_id,
                    "source": block.source,
                    "page": block.page,
                    "section_path": block.section_path,
                    "text": block.text,
                }
            )
    return resolved


def composition_context(
    study: StudyExtraction, blocks: list[EvidenceBlock]
) -> list[dict[str, object]]:
    """Build one compact composition target per reported absorber formula."""

    by_id = {block.block_id: block for block in blocks}
    return [
        {
            "family_id": family.family_id,
            "absorber_id": absorber.absorber_id,
            "absorber_label": absorber.label,
            "layer_id": absorber.layer_id,
            "reported_formula": absorber.formula.raw_value,
            "reported_constituents": [
                {"name": item.name, "role": item.role} for item in absorber.constituents
            ],
            "evidence": _local_evidence(
                _citations_for_absorber(family, absorber), by_id
            ),
        }
        for family in study.device_families
        for absorber in family.absorbers
        if absorber.formula is not None
    ]


def processing_context(
    study: StudyExtraction, blocks: list[EvidenceBlock]
) -> list[dict[str, object]]:
    """Build a compact request whose indices point to existing process data."""

    by_id = {block.block_id: block for block in blocks}
    payload: list[dict[str, object]] = []
    for family in study.device_families:
        layers = {
            layer.layer_id: {"material": layer.material, "role": layer.role}
            for layer in family.layers
        }
        for step in family.processing_steps:
            if not step.materials and not step.conditions:
                continue
            payload.append(
                {
                    "family_id": family.family_id,
                    "step_id": step.step_id,
                    "operation": step.operation,
                    "target_layers": [
                        {"layer_id": layer_id, **layers.get(layer_id, {})}
                        for layer_id in step.target_layer_ids
                    ],
                    "materials": [
                        {"material_index": index, "name": name}
                        for index, name in enumerate(step.materials)
                    ],
                    "conditions": [
                        {
                            "condition_index": index,
                            "name": value.name,
                            "raw_value": value.raw_value,
                            "value_number": value.value_number,
                            "unit": value.unit,
                        }
                        for index, value in enumerate(step.conditions)
                    ],
                    "evidence": _local_evidence(_citations_for_step(step), by_id),
                }
            )
    return payload


_FORMULA_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉₋⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-0123456789-")

_ELEMENT_SYMBOLS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu "
    "Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs "
    "Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl "
    "Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs "
    "Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split()
)


def _valid_formula_token(value: str) -> bool:
    """Reject arbitrary character splits while allowing elements and source acronyms."""

    return value in _ELEMENT_SYMBOLS or bool(
        re.fullmatch(r"[A-Z]{2,}[A-Za-z0-9+-]*", value)
    )


def _formula_key(value: str) -> str:
    """Remove typographic presentation while retaining chemical token order."""

    normalized = (
        unicodedata.normalize("NFKC", value)
        .translate(_FORMULA_TRANSLATION)
        .replace("−", "-")
    )
    return re.sub(r"[^A-Za-z0-9.+-]+", "", normalized)


def _formula_candidates(ions: list[ProposedIon]) -> set[str]:
    """Render explicit and implicit-one forms for exact source-formula comparison."""

    ordered = [ion for site in ("A", "B", "X") for ion in ions if ion.site == site]
    explicit = "".join(f"{ion.abbreviation}{ion.coefficient}" for ion in ordered)
    implicit = "".join(
        ion.abbreviation
        if ion.coefficient == "1"
        else f"{ion.abbreviation}{ion.coefficient}"
        for ion in ordered
    )
    return {_formula_key(explicit), _formula_key(implicit)}


def _reported_formula_candidates(value: str) -> set[str]:
    """Canonicalize presentation variants without interpreting chemical identity.

    Mixed-halide perovskites commonly report fractional X-site occupancy inside a
    final parenthesized group followed by the site multiplicity, for example
    ``Pb(I0.7Br0.3)3``. Enrichment deliberately copies the fractional coefficients,
    so the trailing ``3`` is presentation rather than another ion coefficient. Both
    forms are retained; no token is renamed, reordered, or inferred.
    """

    normalized = unicodedata.normalize("NFKC", value).translate(_FORMULA_TRANSLATION)
    candidates = {_formula_key(normalized)}
    final_group = re.search(r"\(([^()]*)\)\s*([0-9]+(?:\.[0-9]+)?)\s*$", normalized)
    fractional_terms = (
        re.findall(r"[A-Za-z]+0?\.\d+", final_group.group(1)) if final_group else []
    )
    if final_group and len(fractional_terms) >= 2 and final_group.group(2).isdigit():
        candidates.add(
            _formula_key(
                normalized[: final_group.start()]
                + final_group.group(1)
                + normalized[final_group.end() :]
            )
        )
    return candidates


def _formula_reconstructs(value: str, ions: list[ProposedIon]) -> bool:
    """Return whether copied site terms match a conservative reported-form candidate."""

    return bool(_reported_formula_candidates(value) & _formula_candidates(ions))


def _reported_value_is_grounded(
    value: ReportedValue, blocks: dict[str, EvidenceBlock]
) -> bool:
    """Require both the citations and atomic raw value to occur in their source."""

    references = [citation.model_dump(mode="json") for citation in value.evidence]
    citations_valid = bool(references) and all(
        (block := blocks.get(reference["block_id"])) is not None
        and source_contains_text(block.text, reference["quote"])
        for reference in references
    )
    raw_is_direct = any(
        (block := blocks.get(reference["block_id"])) is not None
        and source_contains_text(block.text, value.raw_value)
        for reference in references
    )
    return citations_valid and (
        raw_is_direct or assembled_from_quotes(value.raw_value, references)
    )


def composition_target_id(
    study: StudyExtraction, proposal: CompositionProposal
) -> str | None:
    """Resolve a legacy family-only proposal only when that family has one formula."""

    if proposal.absorber_id is not None:
        return proposal.absorber_id
    family = next(
        (
            candidate
            for candidate in study.device_families
            if candidate.family_id == proposal.family_id
        ),
        None,
    )
    formula_absorbers = (
        [absorber for absorber in family.absorbers if absorber.formula is not None]
        if family
        else []
    )
    return formula_absorbers[0].absorber_id if len(formula_absorbers) == 1 else None


def validate_composition_proposals(
    study: StudyExtraction,
    response: CompositionProposalResponse,
    blocks: list[EvidenceBlock] | None = None,
) -> list[CompositionProposalResult]:
    """Accept site assignments only when they exactly reconstruct a reported formula."""

    families = {family.family_id: family for family in study.device_families}
    absorbers = {
        absorber.absorber_id: (family, absorber)
        for family in study.device_families
        for absorber in family.absorbers
    }
    block_by_id = {block.block_id: block for block in blocks or []}
    seen: set[str] = set()
    results: list[CompositionProposalResult] = []
    for proposal in response.proposals:
        issues: list[str] = []
        family = families.get(proposal.family_id)
        target_id = composition_target_id(study, proposal)
        target = absorbers.get(target_id or "")
        absorber_id = target[1].absorber_id if target else proposal.absorber_id
        if family is None:
            issues.append("family_id does not exist in extraction.json")
            status: ProposalStatus = "rejected"
        elif target is None:
            issues.append("absorber_id does not exist in extraction.json")
            status = "rejected"
        elif target[0].family_id != proposal.family_id:
            issues.append("absorber_id does not belong to the supplied family_id")
            status = "rejected"
        elif absorber_id in seen:
            issues.append("more than one proposal targets this absorber_id")
            status = "rejected"
        elif target[1].formula is None:
            issues.append("the absorber has no reported formula")
            status = "rejected"
        elif blocks is not None and not _reported_value_is_grounded(
            target[1].formula, block_by_id
        ):
            issues.append("the reported formula is not grounded in its cited evidence")
            status = "needs_review"
        elif any(
            not any(ion.site == site for ion in proposal.ions)
            for site in ("A", "B", "X")
        ):
            issues.append("a complete A/B/X site assignment was not proposed")
            status = "needs_review"
        elif any(not _valid_formula_token(ion.abbreviation) for ion in proposal.ions):
            issues.append(
                "one or more ion abbreviations are neither element symbols nor intact acronyms"
            )
            status = "needs_review"
        elif not _formula_reconstructs(target[1].formula.raw_value, proposal.ions):
            issues.append(
                "the assigned ions do not exactly reconstruct the reported formula"
            )
            status = "needs_review"
        else:
            status = "accepted"
        if absorber_id:
            seen.add(absorber_id)
        results.append(
            CompositionProposalResult(proposal=proposal, status=status, issues=issues)
        )
    return results


def _steps(study: StudyExtraction) -> dict[str, ProcessingStep]:
    """Index uniquely named source steps; duplicate IDs remain deliberately unusable."""

    indexed: dict[str, ProcessingStep] = {}
    duplicates: set[str] = set()
    for family in study.device_families:
        for step in family.processing_steps:
            if step.step_id in indexed:
                duplicates.add(step.step_id)
            else:
                indexed[step.step_id] = step
    for step_id in duplicates:
        indexed.pop(step_id, None)
    return indexed


def validate_processing_proposals(
    study: StudyExtraction,
    response: ProcessingProposalResponse,
    blocks: list[EvidenceBlock] | None = None,
) -> list[ProcessingProposalResult]:
    """Accept only unique in-range pointers whose source units fit their target fields."""

    steps = _steps(study)
    block_by_id = {block.block_id: block for block in blocks or []}
    seen: set[str] = set()
    results: list[ProcessingProposalResult] = []
    for proposal in response.proposals:
        issues: list[str] = []
        step = steps.get(proposal.step_id)
        if step is None:
            issues.append("step_id is missing or not unique in extraction.json")
            status: ProposalStatus = "rejected"
        elif proposal.step_id in seen:
            issues.append("more than one proposal targets this step_id")
            status = "rejected"
        else:
            used_conditions: set[int] = set()
            used_fields: set[str] = set()
            used_materials: set[int] = set()
            used_concentrations: set[int] = set()
            for assignment in proposal.condition_assignments:
                index = assignment.condition_index
                if index >= len(step.conditions):
                    issues.append(f"condition_index {index} is out of range")
                    continue
                if index in used_conditions or assignment.target_field in used_fields:
                    issues.append(
                        "condition and target-field mappings must be one-to-one"
                    )
                    continue
                used_conditions.add(index)
                used_fields.add(assignment.target_field)
                value = step.conditions[index]
                if blocks is not None and not _reported_value_is_grounded(
                    value, block_by_id
                ):
                    issues.append(
                        f"condition_index {index} is not grounded in its cited evidence"
                    )
                target_unit = {
                    "temperature": "degree_Celsius",
                    "duration": "second",
                }.get(assignment.target_field)
                if target_unit and convert_reported_value(value, target_unit) is None:
                    issues.append(
                        f"condition_index {index} has no explicit {target_unit}-compatible value"
                    )
                if (
                    assignment.target_field == "atmosphere"
                    and assignment.atmosphere is None
                ):
                    issues.append("an atmosphere mapping requires an atmosphere value")
                if (
                    assignment.target_field == "atmosphere"
                    and value.value_number is not None
                ):
                    issues.append(
                        "a numeric condition cannot be accepted automatically as an atmosphere"
                    )
                if (
                    assignment.target_field != "atmosphere"
                    and assignment.atmosphere is not None
                ):
                    issues.append("atmosphere must be null for numeric target fields")
            for assignment in proposal.material_assignments:
                index = assignment.material_index
                if index >= len(step.materials):
                    issues.append(f"material_index {index} is out of range")
                    continue
                if index in used_materials:
                    issues.append(f"material_index {index} is assigned more than once")
                used_materials.add(index)
                if blocks is not None and not any(
                    (block := block_by_id.get(block_id)) is not None
                    and source_contains_text(block.text, step.materials[index])
                    for block_id in _citations_for_step(step)
                ):
                    issues.append(
                        f"material_index {index} is not present in the step's cited evidence"
                    )
                concentration = assignment.concentration_condition_index
                if concentration is not None:
                    if concentration in used_concentrations:
                        issues.append(
                            f"concentration_condition_index {concentration} is assigned more than once"
                        )
                    used_concentrations.add(concentration)
                    if concentration in used_conditions:
                        issues.append(
                            f"condition_index {concentration} cannot serve as both a process field and concentration"
                        )
                    if assignment.role != "solute":
                        issues.append("only a solute may reference a concentration")
                    elif not 0 <= concentration < len(step.conditions):
                        issues.append(
                            f"concentration_condition_index {concentration} is out of range"
                        )
                    elif step.conditions[concentration].value_number is None or not (
                        is_concentration_unit(step.conditions[concentration].unit)
                    ):
                        issues.append(
                            "a concentration must reference an explicit number and concentration-compatible unit"
                        )
            status = "needs_review" if issues else "accepted"
        seen.add(proposal.step_id)
        results.append(
            ProcessingProposalResult(proposal=proposal, status=status, issues=issues)
        )
    return results


def run_enrichment(
    *,
    client: ModelClient,
    study: StudyExtraction,
    blocks: list[EvidenceBlock],
    model: str,
    reasoning_effort: str | None,
    max_output_tokens: int,
) -> EnrichmentAudit:
    """Interpret local records, retrying only composition targets omitted once.

    The retry never expands context: it contains only parser blocks already local to
    the missing absorbers. This recovers model omissions without paying for or risking
    another full-paper pass.
    """

    from .client import ModelCallError

    errors: list[str] = []
    composition_results: list[CompositionProposalResult] = []
    processing_results: list[ProcessingProposalResult] = []
    composition_input = composition_context(study, blocks)
    composition_call_succeeded = False
    if composition_input:
        try:
            response = client.complete(
                kind="composition_enrichment",
                slug="composition_enrichment",
                model=model,
                system=ENRICHMENT_SYSTEM_PROMPT,
                prompt=COMPOSITION_ENRICHMENT_PROMPT
                + "\n\nEXTRACTED FAMILIES AND LOCAL EVIDENCE:\n"
                + json.dumps(composition_input, ensure_ascii=False),
                response_model=CompositionProposalResponse,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
            )
        except ModelCallError as exc:
            errors.append(f"composition_enrichment: {exc}")
        else:
            composition_call_succeeded = True
            composition_results = validate_composition_proposals(
                study, response, blocks
            )
    resolved_compositions = {
        target
        for result in composition_results
        if (target := composition_target_id(study, result.proposal)) is not None
    }
    missing_composition_input = [
        item
        for item in composition_input
        if str(item["absorber_id"]) not in resolved_compositions
    ]
    if composition_call_succeeded and missing_composition_input:
        try:
            retry_response = client.complete(
                kind="composition_enrichment_retry",
                slug="composition_enrichment_retry",
                model=model,
                system=ENRICHMENT_SYSTEM_PROMPT,
                prompt=COMPOSITION_ENRICHMENT_PROMPT
                + "\n\nThe first pass omitted these absorbers. Interpret only these "
                "targets; an empty proposal list is correct if their formulas remain "
                "ambiguous.\n\nOMITTED ABSORBERS AND LOCAL EVIDENCE:\n"
                + json.dumps(missing_composition_input, ensure_ascii=False),
                response_model=CompositionProposalResponse,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
            )
        except ModelCallError as exc:
            errors.append(f"composition_enrichment_retry: {exc}")
        else:
            retry_results = validate_composition_proposals(
                study, retry_response, blocks
            )
            composition_results.extend(
                result
                for result in retry_results
                if composition_target_id(study, result.proposal)
                not in resolved_compositions
            )
    processing_input = processing_context(study, blocks)
    if processing_input:
        try:
            response = client.complete(
                kind="processing_enrichment",
                slug="processing_enrichment",
                model=model,
                system=ENRICHMENT_SYSTEM_PROMPT,
                prompt=PROCESSING_ENRICHMENT_PROMPT
                + "\n\nEXTRACTED STEPS AND LOCAL EVIDENCE:\n"
                + json.dumps(processing_input, ensure_ascii=False),
                response_model=ProcessingProposalResponse,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
            )
        except ModelCallError as exc:
            errors.append(f"processing_enrichment: {exc}")
        else:
            processing_results = validate_processing_proposals(study, response, blocks)
    return EnrichmentAudit(
        composition_results=composition_results,
        processing_results=processing_results,
        unresolved_absorber_ids=sorted(
            {str(item["absorber_id"]) for item in composition_input}
            - {
                target
                for result in composition_results
                if (target := composition_target_id(study, result.proposal)) is not None
            }
        ),
        unresolved_processing_step_ids=sorted(
            {str(item["step_id"]) for item in processing_input}
            - {result.proposal.step_id for result in processing_results}
        ),
        errors=errors,
    )
