"""Evidence-backed records for extracting a complete photovoltaic study.

These models preserve distinctions that the historical flat PERLA schema cannot
represent, especially device identity, measurement protocol, population results,
and multiple stability experiments. They deliberately contain generic reported values
instead of one field per possible processing or material property.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
EntityKind = Literal[
    "device_family",
    "individual_device",
    "performance_observation",
    "population_statistic",
    "stability_test",
]
STUDY_SCHEMA_VERSION = 4


class StrictModel(BaseModel):
    """Reject unexpected fields so model-output and schema drift stay visible."""

    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceBlock(StrictModel):
    """Represent the smallest parser unit that should not be split further."""

    block_id: Identifier
    source: str
    page: int = Field(ge=1)
    section_path: list[str] = Field(default_factory=list, max_length=20)
    kind: str
    text: str
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    metadata: dict[str, object] = Field(default_factory=dict)

    @property
    def character_count(self) -> int:
        """Return a backend-independent estimate used to bound model calls."""

        return len(self.text)


class EvidenceCitation(StrictModel):
    """Point to a source block and quote the text that supports a claim."""

    block_id: Identifier
    quote: Annotated[str, Field(min_length=1, max_length=1600)]


class ReportedValue(StrictModel):
    """Keep one semantic quantity verbatim and normalize it only when unambiguous.

    A value may include its reported uncertainty or range, but it must not pack
    different quantities or table columns into one string. Atomic values keep metrics
    queryable without prescribing a vocabulary of photovoltaic properties.
    """

    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="Name exactly one reported semantic quantity.",
        ),
    ]
    raw_value: Annotated[
        str,
        Field(
            min_length=1,
            max_length=800,
            description=(
                "Verbatim value of one quantity. A reported uncertainty or range may "
                "remain attached, but different metrics or table columns must be "
                "separate ReportedValue objects."
            ),
        ),
    ]
    value_number: float | None
    unit: Annotated[str | None, Field(max_length=120)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=8)]


class MaterialConstituent(StrictModel):
    """Keep each reported chemical separate so composition remains queryable."""

    name: ShortText
    role: Annotated[str | None, Field(max_length=300)]
    amount: ReportedValue | None
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=8)]


MaterialForm = Literal[
    "self_assembled_monolayer",
    "monolayer",
    "compact_layer",
    "mesoporous_layer",
    "nanostructured_layer",
    "bulk_heterojunction",
    "other",
    "not_reported",
]


class Layer(StrictModel):
    """Separate a layer's function, constituents, and source-reported physical form.

    A self-assembled monolayer can function as a transport layer and contain several
    chemicals. Keeping those axes separate makes architectures comparable without
    hiding the paper's exact material and form wording.
    """

    layer_id: Identifier
    sequence: Annotated[int | None, Field(ge=1)]
    role: Literal[
        "substrate",
        "transparent_electrode",
        "hole_transport_layer",
        "electron_transport_layer",
        "absorber",
        "interlayer",
        "recombination_layer",
        "buffer_layer",
        "back_electrode",
        "encapsulation",
        "other",
        "not_reported",
    ]
    material: ShortText
    constituents: Annotated[
        list[MaterialConstituent], Field(default_factory=list, max_length=40)
    ]
    material_form_raw: Annotated[str | None, Field(max_length=500)] = None
    material_form: Annotated[
        MaterialForm,
        Field(
            description=(
                "Small normalized physical-form vocabulary; crystallinity, measured "
                "morphology, and deposition method belong in other fields."
            )
        ),
    ] = "not_reported"
    reported_properties: Annotated[list[ReportedValue], Field(max_length=40)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def keep_material_form_source_backed(self) -> Layer:
        """Require raw wording for every normalized form and vice versa."""

        reported = self.material_form != "not_reported"
        if reported != (self.material_form_raw is not None):
            raise ValueError(
                "material_form_raw and a reported material_form must occur together"
            )
        return self


class ProcessingStep(StrictModel):
    """Store one fabrication operation without prescribing property-specific fields.

    Generic ``ReportedValue`` conditions let the schema retain unfamiliar treatments
    and process parameters without adding extraction code for every new property.
    """

    step_id: Identifier
    sequence: Annotated[int | None, Field(ge=1)]
    operation: ShortText
    target_layer_ids: Annotated[list[Identifier], Field(max_length=30)]
    materials: Annotated[list[ShortText], Field(max_length=30)]
    conditions: Annotated[list[ReportedValue], Field(max_length=50)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


class AbsorberComponent(StrictModel):
    """Scope one reported composition to one absorber layer or subcell.

    A device may contain several perovskite absorbers. Keeping their formulas,
    constituents, and properties together prevents tandem recipes from being merged
    into one chemically incoherent family-level composition.
    """

    absorber_id: Identifier
    layer_id: Identifier | None
    label: ShortText
    formula: ReportedValue | None
    properties: Annotated[list[ReportedValue], Field(max_length=60)]
    constituents: Annotated[list[MaterialConstituent], Field(max_length=80)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=15)]


class DeviceFamily(StrictModel):
    """Describe one complete photovoltaic design shared by fabricated specimens.

    Family identity comes from the functional-layer materials, absorber composition,
    and device topology—not every treatment arm or fabrication setting. Performance
    observations link to individual devices rather than directly to this record, so
    family-level structure is not confused with a champion or population result.
    """

    family_id: Identifier
    label: ShortText
    variant: Annotated[str | None, Field(max_length=500)]
    architecture: Annotated[str | None, Field(max_length=800)]
    polarity: Literal["n-i-p", "p-i-n", "tandem", "other", "not_reported"]
    full_stack_raw: Annotated[str | None, Field(max_length=1600)]
    layers: Annotated[list[Layer], Field(max_length=60)]
    absorbers: Annotated[list[AbsorberComponent], Field(max_length=12)]
    processing_steps: Annotated[list[ProcessingStep], Field(max_length=150)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=15)]

    @model_validator(mode="before")
    @classmethod
    def migrate_unscoped_absorber(cls, value: object) -> object:
        """Read version-1 records without exposing legacy fields in the new schema.

        Migration is deliberately lossless but not interpretive: all former absorber
        claims become one unscoped component. Splitting a historical tandem record
        still requires source review because doing so deterministically would invent
        which constituent belongs to which subcell.
        """

        if not isinstance(value, dict):
            return value
        legacy_keys = (
            "absorber_formula",
            "absorber_properties",
            "absorber_constituents",
        )
        if not any(key in value for key in legacy_keys):
            return value
        data = dict(value)
        formula = data.pop("absorber_formula", None)
        properties = data.pop("absorber_properties", [])
        constituents = data.pop("absorber_constituents", [])
        if "absorbers" in data:
            if formula is not None or properties or constituents:
                raise ValueError("cannot mix scoped and legacy absorber fields")
            return data
        if formula is None and not properties and not constituents:
            data["absorbers"] = []
            return data

        citations: list[dict[str, object]] = []
        for claim in [formula, *properties, *constituents]:
            claim_data = (
                claim.model_dump(mode="json")
                if isinstance(claim, BaseModel)
                else claim
            )
            if isinstance(claim_data, dict):
                citations.extend(
                    item
                    for item in claim_data.get("evidence", [])
                    if isinstance(item, dict)
                )
        if not citations:
            citations.extend(
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else item
                for item in data.get("evidence", [])
                if isinstance(item, (dict, BaseModel))
            )
        unique_citations = list(
            {
                (str(item.get("block_id")), str(item.get("quote"))): item
                for item in citations
            }.values()
        )[:15]
        absorber_layers = [
            layer.model_dump(mode="json")
            if isinstance(layer, BaseModel)
            else layer
            for layer in data.get("layers", [])
            if (
                layer.role == "absorber"
                if isinstance(layer, Layer)
                else isinstance(layer, dict) and layer.get("role") == "absorber"
            )
        ]
        layer = absorber_layers[0] if len(absorber_layers) == 1 else None
        family_id = str(data.get("family_id", "family"))
        data["absorbers"] = [
            {
                "absorber_id": f"{family_id[:188]}-absorber-1",
                "layer_id": layer.get("layer_id") if layer else None,
                "label": str(layer.get("material")) if layer else "Unscoped absorber",
                "formula": formula,
                "properties": properties,
                "constituents": constituents,
                "evidence": unique_citations,
            }
        ]
        return data


class IndividualDevice(StrictModel):
    """Identify a measured device without treating a population as a device.

    ``reported_properties`` holds values that distinguish this specimen from its
    family, such as one row's fabrication setting. Keeping those values here avoids
    turning a table of device variants into contradictory family-wide conditions.
    """

    device_id: Identifier
    family_id: Identifier | None
    label: ShortText
    variant: Annotated[str | None, Field(max_length=500)]
    champion_status: Literal["yes", "no", "not_reported"]
    selection_basis: Literal["champion", "representative", "other", "not_reported"]
    reported_properties: Annotated[
        list[ReportedValue], Field(default_factory=list, max_length=60)
    ]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


class PerformanceObservation(StrictModel):
    """Keep one protocol-specific measurement linked to one individual device.

    Reverse and forward scans, stabilized output, certification, and EQE-derived
    current remain separate observations even when they concern the same cell. The
    record represents a reported outcome, not merely a statement that an experiment
    was performed.
    """

    observation_id: Identifier
    device_id: Identifier
    measurement_type: Literal[
        "jv_scan",
        "stabilized_power_output",
        "certified_measurement",
        "eqe_integrated_current",
        "other",
        "not_reported",
    ]
    scan_direction: Literal["reverse", "forward", "not_applicable", "not_reported"]
    metrics: Annotated[list[ReportedValue], Field(min_length=1, max_length=40)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


class PopulationStatistic(StrictModel):
    """Prevent a reported aggregate from being mistaken for an individual device.

    Sample size and statistic type preserve whether values are means, medians, ranges,
    distributions, or another population-level summary.
    """

    population_id: Identifier
    family_id: Identifier | None
    label: ShortText
    statistic_type: Literal[
        "mean",
        "median",
        "minimum",
        "maximum",
        "standard_deviation",
        "range",
        "distribution",
        "other",
        "not_reported",
    ]
    sample_size: Annotated[int | None, Field(ge=1)]
    metrics: Annotated[list[ReportedValue], Field(min_length=1, max_length=40)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


class StabilityCheckpoint(StrictModel):
    """Preserve conditions and outcomes that apply at one point or aging stage.

    A protocol can change temperature, atmosphere, illumination, or another condition
    between stages. Storing those values beside the checkpoint prevents a compound
    test-level string from hiding which condition applied to which outcome.
    """

    checkpoint_id: Identifier
    time: ReportedValue | None
    conditions: Annotated[
        list[ReportedValue], Field(default_factory=list, max_length=40)
    ]
    outcomes: Annotated[list[ReportedValue], Field(min_length=1, max_length=40)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


class StabilityTest(StrictModel):
    """Keep aging conditions and checkpoints distinct from performance observations.

    ``link_status`` makes unsupported device identity explicit: a test may link to an
    individual device, only to a family, or remain a separate stability specimen.
    """

    test_id: Identifier
    family_id: Identifier | None
    device_id: Identifier | None
    specimen_label: ShortText
    link_status: Literal[
        "explicit_device_link",
        "explicit_family_link",
        "stability_specimen_only",
        "not_reported",
    ]
    conditions: Annotated[list[ReportedValue], Field(max_length=60)]
    checkpoints: Annotated[
        list[StabilityCheckpoint], Field(min_length=1, max_length=80)
    ]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


class PaperMetadata(StrictModel):
    """Identify the supplied paper without trying to infer missing metadata."""

    title: Annotated[str | None, Field(max_length=800)]
    doi: Annotated[str | None, Field(max_length=300)]


class CrossWindowIdentityLink(StrictModel):
    """Link window candidates that denote the same real-world entity.

    Candidates remain intact and auditable. The link communicates identity without
    selecting one candidate or heuristically merging possibly conflicting details.
    """

    link_id: Identifier
    entity_kind: EntityKind
    candidate_ids: Annotated[list[Identifier], Field(min_length=2, max_length=100)]
    rationale: ShortText
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def validate_candidate_ids(self) -> CrossWindowIdentityLink:
        """Reject duplicate candidates because one link denotes one identity set."""

        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("candidate_ids must be unique")
        return self


class StudyExtraction(StrictModel):
    """Represent all supported study entities without flattening reporting levels.

    The model deliberately keeps families, individual devices, protocol-specific
    observations, population statistics, and stability tests in separate collections.
    Windowed extraction may add identity links, but candidates remain intact so
    linking cannot silently discard conflicting evidence.
    """

    paper: PaperMetadata
    device_families: list[DeviceFamily]
    individual_devices: list[IndividualDevice]
    performance_observations: list[PerformanceObservation]
    population_statistics: list[PopulationStatistic]
    stability_tests: list[StabilityTest]
    identity_links: list[CrossWindowIdentityLink] = Field(default_factory=list)
    unresolved_notes: list[ShortText]


def study_schema_sha256() -> str:
    """Fingerprint the generated schema so provenance cannot depend on manual bumps."""

    encoded = json.dumps(
        StudyExtraction.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
