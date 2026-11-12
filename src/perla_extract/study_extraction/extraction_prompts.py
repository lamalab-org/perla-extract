"""Scientific instructions and evidence serialization for reading and assembly.

Changes here change the experiment's prompt fingerprint. Keep orchestration,
provider settings, and persistence outside this module.
"""

from __future__ import annotations

import hashlib
import json

from .claims import ClaimLedger, assembly_evidence_payload
from .enrichment import (
    COMPOSITION_ENRICHMENT_PROMPT,
    ENRICHMENT_SYSTEM_PROMPT,
    PROCESSING_ENRICHMENT_PROMPT,
)
from .guidance import (
    COMPOSITION_BOUNDARY_POLICY,
    DEVICE_FAMILY_POLICY,
    EVIDENCE_INTERPRETATION_POLICY,
    RECORD_BOUNDARY_POLICY,
    SHARED_QUANTITY_POLICY,
)
from .models import EvidenceBlock
from .refinement import REFINEMENT_PROMPT
from .repair import REPAIR_PROMPT, REPAIR_SYSTEM_PROMPT
from .spans import build_evidence_spans, evidence_payload
from .transport import compact_to_span_citations

SYSTEM_PROMPT = """Extract the complete present photovoltaic study as device-centered data.
Use only supplied evidence. Preserve source wording and distinctions. Never invent,
interpolate, digitize graph traces, or import values from cited literature."""

EXTRACTION_PROMPT = f"""Read all supplied evidence before extracting.

Identify every distinct photovoltaic device family made in the present study. Then
identify every individually measured device, performance
observation, population statistic, and stability specimen. Connect records only when
the evidence supports the connection.

{DEVICE_FAMILY_POLICY}
{RECORD_BOUNDARY_POLICY}
{COMPOSITION_BOUNDARY_POLICY}
{EVIDENCE_INTERPRETATION_POLICY}
{SHARED_QUANTITY_POLICY}
Rules:
- Preserve a device as one coherent object. Do not create one device per metric,
  paragraph, layer, scan direction, or stability checkpoint.
- Put a fabrication or material value that distinguishes one measured specimen in
  that IndividualDevice's reported_properties, not among family-wide conditions.
- Keep individual devices and population statistics in separate top-level arrays.
  Means, medians, distributions, sample sizes, ranges, and population maxima are not
  individual-device measurements.
- Mark a device champion only when the source explicitly identifies the device as a
  champion/best-performing device or its efficiency as record/highest. An extremum
  for one attribute (for example voltage, current, or stability) does not make the
  device a champion. Otherwise use not_reported.
- Keep reverse scans, forward scans, stabilized output, certified measurements, and
  EQE-integrated currents as distinct observations of the same device where supported.
- A performance observation requires at least one reported result. A statement that a
  measurement or spectrum exists, without any reported outcome, is context rather than
  a performance observation. Non-numeric outcomes are valid when the source actually
  reports them; do not use a method name or specimen description as a metric value.
- Extract the complete ordered layer stack and all reported processing steps. Create
  one scoped absorber record per absorber layer or subcell, keeping each absorber's
  formula, constituents, properties, additives, and dopants together. Never combine
  wide-bandgap and narrow-bandgap tandem chemistry into one composition.
  Use one MaterialConstituent per named chemical; do not combine a list of chemicals
  into one constituent.
- For every layer, keep its electrical role, chemical constituents, and physical form
  separate. Copy material_form_raw only when the source explicitly names the form,
  then select the closest allowed material_form. Use other for an explicit form that
  is outside the vocabulary and not_reported when the source states no form. A known
  material name alone is not evidence that it is a self-assembled monolayer.
  self_assembled_monolayer requires explicit SAM or self-assembled-monolayer wording;
  monolayer requires explicit monolayer wording without a self-assembly claim;
  compact_layer, mesoporous_layer, nanostructured_layer, and bulk_heterojunction
  likewise require corresponding source wording. Do not derive form from deposition
  method.
- Put arbitrary reported material, geometry, processing, measurement, and stability
  values in generic ReportedValue records. Do not omit a value because it lacks a
  dedicated schema field.
- Each ReportedValue must contain exactly one semantic quantity. A reported
  uncertainty or range may stay with that quantity, but never pack different metrics,
  table columns, or a complete row into one name or raw_value. Emit one object per
  quantity even when several quantities share the same source row.
- ReportedValue.raw_value and material_form_raw must be copied from supplied evidence.
  Every evidence array contains supplied span_id values, never copied quotation text
  or block IDs. Reuse a span_id when several atomic values share one row or sentence.
  Typography-preserving renderings are source evidence and may be used for subscript
  or superscript notation.
- Stability tests remain separate experiments with ordered checkpoints, even when
  linked to a performance device. Put a condition that changes between aging stages
  in the corresponding checkpoint.conditions; test.conditions contains only values
  that apply to the complete test.
- Exclude background examples, prior literature, reference entries, review material,
  and devices not made or measured in this study.
- Use null or not_reported for unknown identity or context. Do not guess.
- Empty arrays are correct only when all supplied evidence truly contains no record of
  that kind.
- Reconcile duplicate mentions before assigning final record IDs.
"""

CLAIM_LEDGER_PROMPT = f"""Read the supplied paper evidence before the final schema is
assembled. Return a neutral ledger of experimental objects and source claims, not a
draft StudyExtraction.

{DEVICE_FAMILY_POLICY}
{RECORD_BOUNDARY_POLICY}
{COMPOSITION_BOUNDARY_POLICY}
{EVIDENCE_INTERPRETATION_POLICY}
{SHARED_QUANTITY_POLICY}
Rules:
- Describe what each source-mentioned object is used for: a reusable device design,
  individual device, processing arm, characterization specimen, population,
  performance measurement, stability experiment, or other object. Do not promote an
  object to a device family merely because it has a label.
- Mark an object or claim target only when its facts belong in the present photovoltaic
  study extraction. Mark background, method-only, and characterization-only facts as
  context. Use uncertain when the evidence does not resolve scope.
- Emit one atomic claim per assertion. Use kind=reported_quantity for every numerical
  or non-numerical value that should become a ReportedValue. Its raw_value must contain
  only that one exact source value or outcome, not the surrounding procedure.
- When one explicit source quantity applies grammatically to several named materials
  or metrics, emit one reported_quantity claim whose raw_value is only the shared
  quantity and whose shared_targets contains every named target. For example, source
  text assigning 1.4 M to PbI2, MAI, and DMSO has raw_value="1.4 M" and three targets.
  If one sentence reports different quantities, emit separate claims; never assign a
  solvent or material to a quantity that the grammar does not give it. Do not infer
  shared scope from chemistry or proximity.
- Connect a claim to every experimental object it concerns. Repeated mentions and
  treatment labels may refer to the same underlying design; leave that reconciliation
  to the later document-level assembly call.

Every object and claim needs a supplied evidence span ID. Never invent span IDs.
"""

CLAIM_LEDGER_GUIDANCE = """The supplied claim ledger is a fallible map of the source,
not source evidence and not a list of records to create. Reconcile duplicate mentions
globally. A target device-design object may support a device family; a treatment arm
or characterization specimen does not. For every supported target claim, represent it
at the correct reporting level or explain why it remains unresolved. Context claims
must not create output records. Preserve explicit shared_targets as separate atomic
values in the final schema."""

CLAIM_RECALL_PROMPTS = (
    """INDEPENDENT OBJECT-FIRST READING: Start from the primary evidence again. First
enumerate every distinct experimental object, specimen, comparison group, and reporting
scope; then attach every supported atomic claim. Do not assume another reading captured
anything.""",
    """INDEPENDENT CLAIM-FIRST READING: Start from the primary evidence again. Scan each
sentence, table row, and caption for atomic values, conditions, relations, and explicit
scope; then attach them to supported objects. Do not assume another reading captured
anything.""",
)


def prompt_sha256() -> str:
    """Fingerprint every prompt template that can change scientific model output."""

    encoded = json.dumps(
        [
            SYSTEM_PROMPT,
            EXTRACTION_PROMPT,
            CLAIM_LEDGER_PROMPT,
            CLAIM_LEDGER_GUIDANCE,
            *CLAIM_RECALL_PROMPTS,
            ENRICHMENT_SYSTEM_PROMPT,
            COMPOSITION_ENRICHMENT_PROMPT,
            PROCESSING_ENRICHMENT_PROMPT,
            REFINEMENT_PROMPT,
            REPAIR_SYSTEM_PROMPT,
            REPAIR_PROMPT,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ledger_payload(ledger: ClaimLedger, blocks: list[EvidenceBlock]) -> str:
    """Serialize the grounded claim ledger with compact evidence references."""

    return json.dumps(
        compact_to_span_citations(ledger, build_evidence_spans(blocks)),
        ensure_ascii=False,
    )


def _direct_prompt(blocks: list[EvidenceBlock], ledger: ClaimLedger) -> str:
    """Build a study from complete source or reader-selected statements and text."""

    if not ledger.objects and not ledger.claims:
        return (
            EXTRACTION_PROMPT
            + "\n\nCOMPLETE SOURCE EVIDENCE:\n"
            + json.dumps(assembly_evidence_payload(blocks, ledger), ensure_ascii=False)
        )
    return (
        EXTRACTION_PROMPT
        + "\n\nSOURCE-GROUNDED CLAIM LEDGER:\n"
        + CLAIM_LEDGER_GUIDANCE
        + "\n"
        + _ledger_payload(ledger, blocks)
        + "\n\nCLAIM-SUPPORTING SOURCE PASSAGES:\n"
        + json.dumps(assembly_evidence_payload(blocks, ledger), ensure_ascii=False)
    )


def _assembly_components(
    blocks: list[EvidenceBlock], ledger: ClaimLedger
) -> dict[str, object]:
    """Identify the actual request parts for per-stage size diagnostics."""

    has_notes = bool(ledger.objects or ledger.claims)
    return {
        "instructions": EXTRACTION_PROMPT
        + (CLAIM_LEDGER_GUIDANCE if has_notes else ""),
        "source_evidence": assembly_evidence_payload(blocks, ledger),
        "reader_notes": _ledger_payload(ledger, blocks) if has_notes else "",
    }


def _claim_ledger_prompt(
    primary: list[EvidenceBlock],
    context: list[EvidenceBlock] | None = None,
    recall_pass: int = 1,
) -> str:
    """Ask one reading to inventory source statements and their subjects.

    Repeated readings receive the same source and different attention instructions;
    they do not receive earlier model output. Their errors can still be correlated.
    Combining readings offers another chance to find omitted facts, not independent
    scientific confirmation of a statement.
    """

    context = context or []
    return (
        CLAIM_LEDGER_PROMPT
        + (
            "\n\nThis is one part of a long document. Emit an object, claim, or "
            "exclusion only when it cites PRIMARY EVIDENCE; CONTEXT is supplied "
            "only to resolve terminology."
            if context
            else ""
        )
        + ("\n\n" + CLAIM_RECALL_PROMPTS[recall_pass - 2] if recall_pass > 1 else "")
        + "\n\nCONTEXT EVIDENCE:\n"
        + json.dumps(evidence_payload(context), ensure_ascii=False)
        + "\n\nPRIMARY EVIDENCE:\n"
        + json.dumps(evidence_payload(primary), ensure_ascii=False)
    )
