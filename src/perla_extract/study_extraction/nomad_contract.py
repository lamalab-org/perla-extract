"""Pydantic mirror of the pinned NOMAD fields emitted by PERLA Extract.

Keeping this small outbound contract separate makes an upstream schema upgrade a
reviewable data-contract change rather than an accidental change to projection logic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .vocabulary import NormalizedAtmosphere

NOMAD_SCHEMA_PACKAGE = "perovskite-solar-cell-database"
NOMAD_SCHEMA_VERSION = "1.2.14"
NOMAD_SCHEMA_COMMIT = "afd75e69ebb07c8f7f82d203231b70f488e40997"
NOMAD_M_DEF: Literal[
    "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell"
] = "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell"

SourceKind = Literal[
    "absorber",
    "device_family",
    "individual_device",
    "performance_observation",
    "population_statistic",
    "processing_step",
    "stability_test",
]
CompositionStatus = Literal["ready", "partial", "not_reported", "needs_review"]


class _StrictModel(BaseModel):
    """Keep our pinned outbound contract from silently accepting new fields."""

    model_config = ConfigDict(extra="forbid")


class NOMADConversionIssue(_StrictModel):
    """Explain one field that was omitted or retained outside a NOMAD field."""

    code: str
    source_kind: SourceKind
    source_id: str
    path: str | None = None
    detail: str


class NOMADRecordMapping(_StrictModel):
    """Connect an atomic rich record to the standalone archive written for it."""

    source_kind: SourceKind
    source_id: str
    archive_index: int = Field(ge=0)
    archive_file: str


class NOMADIon(_StrictModel):
    """Represent an explicitly reported perovskite site ion."""

    abbreviation: str
    coefficient: str | None = None


class NOMADComposition(_StrictModel):
    """Mirror the subset of NOMAD composition fields populated by this adapter."""

    long_form: str | None = None
    formula: str | None = None
    dimensionality: Literal["0D", "1D", "2D", "2D/3D", "3D", "Other"] | None = None
    band_gap: float | None = None
    ions_a_site: list[NOMADIon] = Field(default_factory=list)
    ions_b_site: list[NOMADIon] = Field(default_factory=list)
    ions_x_site: list[NOMADIon] = Field(default_factory=list)


class NOMADCompositionProjection(_StrictModel):
    """Make chemical normalization readiness reviewable before NOMAD ingestion."""

    family_id: str
    absorber_id: str
    status: CompositionStatus
    raw_formula: str | None
    nomad_composition: NOMADComposition | None
    normalizer_package: str = NOMAD_SCHEMA_PACKAGE
    normalizer_version: str = NOMAD_SCHEMA_VERSION
    normalizer_commit: str = NOMAD_SCHEMA_COMMIT
    issues: list[str] = Field(default_factory=list)


class NOMADSolute(_StrictModel):
    """Mirror a named solute and an optional explicitly reported concentration."""

    name: str
    concentration: float | None = None
    concentration_unit: (
        Literal[
            "mol/L",
            "mmol/L",
            "g/L",
            "mg/L",
            "mg/mL",
            "wt%",
            "vol%",
            "M",
            "Unknown",
        ]
        | None
    ) = None


class NOMADSolvent(_StrictModel):
    """Mirror a named solvent without inventing a mixture fraction."""

    name: str
    volume_fraction: float | None = None


class NOMADReactionSolution(_StrictModel):
    """Mirror the solution subset populated from accepted material-role proposals."""

    solutes: list[NOMADSolute] = Field(default_factory=list)
    solvents: list[NOMADSolvent] = Field(default_factory=list)


class NOMADProcessingStep(_StrictModel):
    """Mirror fields the pinned NOMAD processing section accepts."""

    step_name: str | None = None
    method: str | None = None
    atmosphere: NormalizedAtmosphere | None = None
    temperature: float | None = None
    duration: float | None = None
    solution: NOMADReactionSolution | None = None
    antisolvent: str | None = None
    additional_parameters: dict[str, object] | None = None


class NOMADLayer(_StrictModel):
    """Represent one ordered layer in NOMAD's LLM extraction schema."""

    name: str
    thickness: float | None = None
    functionality: (
        Literal[
            "Hole-transport",
            "Electron-transport",
            "Contact",
            "Absorber",
            "Other",
            "Substrate",
            "Unknown",
        ]
        | None
    ) = None
    deposition: list[NOMADProcessingStep] = Field(default_factory=list)


class NOMADStability(_StrictModel):
    """Represent NOMAD's summary view without collapsing rich checkpoints."""

    light_intensity: float | None = None
    time: float | None = None
    humidity: float | None = None
    temperature: float | None = None
    PCE_at_start: float | None = None
    PCE_after_1000_hours: float | None = None
    PCE_at_end: float | None = None


class NOMADExtractionMetadata(_StrictModel):
    """Identify the model responsible for the source extraction."""

    model: str | None = None
    model_version: str | None = None


class NOMADCell(_StrictModel):
    """Validate exactly the outbound archive data populated by PERLA Extract."""

    m_def: Literal[
        "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell"
    ] = NOMAD_M_DEF
    DOI_number: str | None = None
    publication_title: str | None = None
    perovskite_composition: NOMADComposition | None = None
    device_architecture: (
        Literal["pin", "nip", "Back contacted", "Front contacted", "Other", "Unknown"]
        | None
    ) = None
    pce: float | None = None
    jsc: float | None = None
    voc: float | None = None
    ff: float | None = None
    active_area: float | None = None
    number_devices: int | None = None
    averaged_quantities: bool | None = None
    additional_notes: str | None = None
    stability: NOMADStability | None = None
    layers: list[NOMADLayer] = Field(default_factory=list)
    layer_order: str | None = None
    extraction_metadata: NOMADExtractionMetadata | None = None


class NOMADArchive(_StrictModel):
    """Wrap data in NOMAD's uploadable archive envelope."""

    data: NOMADCell


class NOMADExport(_StrictModel):
    """Bundle standalone archives with a machine-readable conversion report."""

    target_package: str = NOMAD_SCHEMA_PACKAGE
    target_version: str = NOMAD_SCHEMA_VERSION
    target_commit: str = NOMAD_SCHEMA_COMMIT
    archives: list[NOMADArchive]
    mappings: list[NOMADRecordMapping]
    composition_projections: list[NOMADCompositionProjection]
    issues: list[NOMADConversionIssue]
