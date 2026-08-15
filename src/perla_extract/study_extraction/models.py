"""Evidence-backed records for extracting a complete photovoltaic study.

These models preserve distinctions that the historical flat PERLA schema cannot
represent, especially device identity, measurement protocol, population results,
and multiple stability experiments.  They deliberately contain generic facts
instead of one field per possible processing or material property.
"""

from __future__ import annotations

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


class EvidenceRef(StrictModel):
    """Point to a source block and quote the text that supports a claim."""

    block_id: Identifier
    quote: Annotated[str, Field(min_length=1, max_length=1600)]


class Fact(StrictModel):
    """Keep a reported value verbatim and normalize it only when unambiguous."""

    name: ShortText
    raw_value: Annotated[str, Field(min_length=1, max_length=800)]
    value_number: float | None
    unit: Annotated[str | None, Field(max_length=120)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=8)]


class MaterialConstituent(StrictModel):
    """Represent a named constituent, precursor, additive, or dopant."""

    name: ShortText
    role: Annotated[str | None, Field(max_length=300)]
    amount: Fact | None
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=8)]


class Layer(StrictModel):
    """Represent one physical layer in the reported stack order."""

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
    details: Annotated[list[Fact], Field(max_length=40)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class ProcessingStep(StrictModel):
    """Represent a fabrication operation using generic reported conditions."""

    step_id: Identifier
    sequence: Annotated[int | None, Field(ge=1)]
    operation: ShortText
    target_layer_ids: Annotated[list[Identifier], Field(max_length=30)]
    materials: Annotated[list[ShortText], Field(max_length=30)]
    conditions: Annotated[list[Fact], Field(max_length=50)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class DeviceFamily(StrictModel):
    """Collect composition and processing shared by one or more devices."""

    family_id: Identifier
    label: ShortText
    variant: Annotated[str | None, Field(max_length=500)]
    architecture: Annotated[str | None, Field(max_length=800)]
    polarity: Literal["n-i-p", "p-i-n", "tandem", "other", "not_reported"]
    full_stack_raw: Annotated[str | None, Field(max_length=1600)]
    layers: Annotated[list[Layer], Field(max_length=60)]
    absorber_formula: Fact | None
    absorber_properties: Annotated[list[Fact], Field(max_length=60)]
    absorber_constituents: Annotated[list[MaterialConstituent], Field(max_length=80)]
    processing_steps: Annotated[list[ProcessingStep], Field(max_length=150)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=15)]


class IndividualDevice(StrictModel):
    """Identify a measured device without treating a population as a device."""

    device_id: Identifier
    family_id: Identifier | None
    label: ShortText
    variant: Annotated[str | None, Field(max_length=500)]
    champion_status: Literal["yes", "no", "not_reported"]
    selection_basis: Literal["champion", "representative", "other", "not_reported"]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class PerformanceObservation(StrictModel):
    """Store one protocol-specific measurement of one individual device."""

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
    metrics: Annotated[list[Fact], Field(min_length=1, max_length=40)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class PopulationStatistic(StrictModel):
    """Keep a population result separate from individual device measurements."""

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
    metrics: Annotated[list[Fact], Field(min_length=1, max_length=40)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class StabilityCheckpoint(StrictModel):
    """Store one point or lifetime metric within a stability experiment."""

    checkpoint_id: Identifier
    time: Fact | None
    outcomes: Annotated[list[Fact], Field(min_length=1, max_length=40)]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class StabilityTest(StrictModel):
    """Keep a stability experiment distinct from JV measurements."""

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
    conditions: Annotated[list[Fact], Field(max_length=60)]
    checkpoints: Annotated[
        list[StabilityCheckpoint], Field(min_length=1, max_length=80)
    ]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=12)]


class Paper(StrictModel):
    """Identify the supplied paper without trying to infer missing metadata."""

    title: Annotated[str | None, Field(max_length=800)]
    doi: Annotated[str | None, Field(max_length=300)]


class EquivalenceGroup(StrictModel):
    """Link window candidates that denote the same real-world entity.

    Candidates remain intact and auditable. The link communicates identity without
    selecting one candidate or heuristically merging possibly conflicting details.
    """

    equivalence_id: Identifier
    entity_kind: EntityKind
    member_ids: Annotated[list[Identifier], Field(min_length=2, max_length=100)]
    rationale: ShortText
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def validate_members(self) -> EquivalenceGroup:
        """Reject repeated members because an equivalence set contains unique IDs."""

        if len(self.member_ids) != len(set(self.member_ids)):
            raise ValueError("member_ids must be unique")
        return self


class StudyExtraction(StrictModel):
    """Hold all extracted candidates from one paper and its supplement."""

    paper: Paper
    device_families: list[DeviceFamily]
    individual_devices: list[IndividualDevice]
    performance_observations: list[PerformanceObservation]
    population_statistics: list[PopulationStatistic]
    stability_tests: list[StabilityTest]
    equivalence_groups: list[EquivalenceGroup] = Field(default_factory=list)
    unresolved_notes: list[ShortText]
