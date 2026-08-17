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
STUDY_SCHEMA_VERSION = 1


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


class Layer(StrictModel):
    """Preserve one physical layer's stack position, role, and source wording."""

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
    reported_properties: Annotated[list[ReportedValue], Field(max_length=40)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=12)]


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


class DeviceFamily(StrictModel):
    """Collect the composition and fabrication shared by a reported device variant.

    Performance observations link to individual devices rather than directly to this
    record, so family-level structure is not confused with a champion or population
    result.
    """

    family_id: Identifier
    label: ShortText
    variant: Annotated[str | None, Field(max_length=500)]
    architecture: Annotated[str | None, Field(max_length=800)]
    polarity: Literal["n-i-p", "p-i-n", "tandem", "other", "not_reported"]
    full_stack_raw: Annotated[str | None, Field(max_length=1600)]
    layers: Annotated[list[Layer], Field(max_length=60)]
    absorber_formula: ReportedValue | None
    absorber_properties: Annotated[list[ReportedValue], Field(max_length=60)]
    absorber_constituents: Annotated[list[MaterialConstituent], Field(max_length=80)]
    processing_steps: Annotated[list[ProcessingStep], Field(max_length=150)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=15)]


class IndividualDevice(StrictModel):
    """Identify a measured device without treating a population as a device."""

    device_id: Identifier
    family_id: Identifier | None
    label: ShortText
    variant: Annotated[str | None, Field(max_length=500)]
    champion_status: Literal["yes", "no", "not_reported"]
    selection_basis: Literal["champion", "representative", "other", "not_reported"]
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
    """Preserve one reported outcome and its time within an ordered stability test."""

    checkpoint_id: Identifier
    time: ReportedValue | None
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
