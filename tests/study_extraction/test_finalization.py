from perla_extract.study_extraction.finalization import (
    remove_unsupported_optional_claims,
)
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    Layer,
    PaperMetadata,
    ReportedValue,
    StudyExtraction,
)
from perla_extract.study_extraction.validation import validate_study


def _study_with_unsupported_optional_claims() -> (
    tuple[StudyExtraction, list[EvidenceBlock]]
):
    block = EvidenceBlock(
        block_id="source",
        source="main",
        page=1,
        kind="paragraph",
        text="The device used PCBM:PMMA as ETL.",
    )
    evidence = [EvidenceCitation(block_id=block.block_id, quote=block.text)]
    layer = Layer(
        layer_id="etl",
        sequence=1,
        role="electron_transport_layer",
        material="PCBM:PMMA",
        constituents=[],
        material_form_raw="blend",
        material_form="other",
        reported_properties=[
            ReportedValue(
                name="thickness",
                raw_value="50 nm",
                value_number=50,
                unit="nm",
                evidence=evidence,
            )
        ],
        evidence=evidence,
    )
    family = DeviceFamily(
        family_id="family",
        label="PCBM:PMMA device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[layer],
        absorbers=[],
        processing_steps=[],
        evidence=evidence,
    )
    return (
        StudyExtraction(
            paper=PaperMetadata(title=None, doi=None),
            device_families=[family],
            individual_devices=[],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[],
        ),
        [block],
    )


def test_finalization_removes_only_unsupported_optional_atomic_claims():
    study, blocks = _study_with_unsupported_optional_claims()

    final, audit = remove_unsupported_optional_claims(study, blocks)

    layer = final.device_families[0].layers[0]
    assert layer.material == "PCBM:PMMA"
    assert layer.material_form_raw is None
    assert layer.material_form == "not_reported"
    assert layer.reported_properties == []
    assert audit["removal_count"] == 2
    assert audit["remaining_issue_count"] == 0
    assert validate_study(final, blocks)["status"] == "verified"
    removed = [item["removed"] for item in audit["removals"]]
    assert "blend" in removed
    assert any(
        isinstance(item, dict) and item.get("raw_value") == "50 nm" for item in removed
    )


def test_finalization_does_not_hide_unknown_evidence_pointers():
    study, blocks = _study_with_unsupported_optional_claims()
    study.device_families[0].layers[0].evidence[0].block_id = "missing"

    final, audit = remove_unsupported_optional_claims(study, blocks)

    assert audit["remaining_issue_count"] > 0
    assert any(
        issue["reason"] == "unknown block_id" for issue in audit["remaining_issues"]
    )
    assert final.device_families[0].layers[0].evidence[0].block_id == "missing"
