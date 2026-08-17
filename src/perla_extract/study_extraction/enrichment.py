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

from pydantic import BaseModel, ConfigDict, Field

from .evidence import assembled_from_quotes, source_contains_text
from .models import (
    DeviceFamily,
    EvidenceBlock,
    ProcessingStep,
    ReportedValue,
    StudyExtraction,
)
from .units import convert_reported_value
from .vocabulary import NormalizedAtmosphere

if TYPE_CHECKING:
    from .client import ModelClient

ENRICHMENT_SYSTEM_PROMPT = """Interpret only the supplied extracted records and their
local source evidence. Never add a device, material, condition, number, or formula.
Return an empty proposal list when the evidence does not support a requested mapping."""

COMPOSITION_ENRICHMENT_PROMPT = """Assign the terms of each reported perovskite
formula to its A, B, and X sites.

Rules:
- Copy ion abbreviations and stoichiometric coefficients from the reported formula.
- Use coefficient "1" only when a coefficient is implicit.
- Preserve the order of terms within each site.
- Do not infer a formula from a layer name or from general chemical knowledge.
- Do not repair or complete a malformed formula.
- Emit at most one proposal for each supplied family_id.
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
    """Group all site assignments proposed for one existing device family."""

    family_id: str = Field(min_length=1, max_length=200)
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
    unresolved_composition_ids: list[str] = Field(default_factory=list)
    unresolved_processing_step_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _citations_for_family(family: DeviceFamily) -> Iterable[str]:
    """Collect only evidence IDs already attached to a family's composition claims."""

    for citation in family.evidence:
        yield citation.block_id
    if family.absorber_formula:
        for citation in family.absorber_formula.evidence:
            yield citation.block_id
    for constituent in family.absorber_constituents:
        for citation in constituent.evidence:
            yield citation.block_id
    for layer in family.layers:
        if layer.role == "absorber":
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
    """Build a compact composition request from reported formulas and cited blocks."""

    by_id = {block.block_id: block for block in blocks}
    return [
        {
            "family_id": family.family_id,
            "reported_formula": family.absorber_formula.raw_value,
            "reported_constituents": [
                {"name": item.name, "role": item.role}
                for item in family.absorber_constituents
            ],
            "evidence": _local_evidence(_citations_for_family(family), by_id),
        }
        for family in study.device_families
        if family.absorber_formula is not None
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


def validate_composition_proposals(
    study: StudyExtraction,
    response: CompositionProposalResponse,
    blocks: list[EvidenceBlock] | None = None,
) -> list[CompositionProposalResult]:
    """Accept site assignments only when they exactly reconstruct a reported formula."""

    families = {family.family_id: family for family in study.device_families}
    block_by_id = {block.block_id: block for block in blocks or []}
    seen: set[str] = set()
    results: list[CompositionProposalResult] = []
    for proposal in response.proposals:
        issues: list[str] = []
        family = families.get(proposal.family_id)
        if family is None:
            issues.append("family_id does not exist in extraction.json")
            status: ProposalStatus = "rejected"
        elif proposal.family_id in seen:
            issues.append("more than one proposal targets this family_id")
            status = "rejected"
        elif family.absorber_formula is None:
            issues.append("the family has no reported absorber formula")
            status = "rejected"
        elif blocks is not None and not _reported_value_is_grounded(
            family.absorber_formula, block_by_id
        ):
            issues.append("the reported formula is not grounded in its cited evidence")
            status = "needs_review"
        elif any(
            not any(ion.site == site for ion in proposal.ions)
            for site in ("A", "B", "X")
        ):
            issues.append("a complete A/B/X site assignment was not proposed")
            status = "needs_review"
        elif _formula_key(family.absorber_formula.raw_value) not in _formula_candidates(
            proposal.ions
        ):
            issues.append(
                "the assigned ions do not exactly reconstruct the reported formula"
            )
            status = "needs_review"
        else:
            status = "accepted"
        seen.add(proposal.family_id)
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
                    if assignment.role != "solute":
                        issues.append("only a solute may reference a concentration")
                    elif concentration >= len(step.conditions):
                        issues.append(
                            f"concentration_condition_index {concentration} is out of range"
                        )
                    elif (
                        step.conditions[concentration].value_number is None
                        or not step.conditions[concentration].unit
                    ):
                        issues.append(
                            "a concentration must reference an explicit number and unit"
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
    """Run at most two compact calls and retain failures without blocking extraction."""

    from .client import ModelCallError

    errors: list[str] = []
    composition_results: list[CompositionProposalResult] = []
    processing_results: list[ProcessingProposalResult] = []
    composition_input = composition_context(study, blocks)
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
            composition_results = validate_composition_proposals(
                study, response, blocks
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
        unresolved_composition_ids=sorted(
            {str(item["family_id"]) for item in composition_input}
            - {result.proposal.family_id for result in composition_results}
        ),
        unresolved_processing_step_ids=sorted(
            {str(item["step_id"]) for item in processing_input}
            - {result.proposal.step_id for result in processing_results}
        ),
        errors=errors,
    )
