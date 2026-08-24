from perla_extract.study_extraction.inventory import (
    EvidenceInventory,
    InventoryItem,
    audit_inventory_coverage,
)
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    PaperMetadata,
    StudyExtraction,
)
from perla_extract.study_extraction.repair import StudyRepair, run_targeted_repair
from perla_extract.study_extraction.validation import validate_study


def empty_study() -> StudyExtraction:
    return StudyExtraction(
        paper=PaperMetadata(title="Example", doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def family(citation: EvidenceCitation) -> DeviceFamily:
    return DeviceFamily(
        family_id="family-b",
        label="Device B",
        variant="B",
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorbers=[],
        processing_steps=[],
        evidence=[citation],
    )


def repair_with_family(item: DeviceFamily) -> StudyRepair:
    return StudyRepair(
        device_families=[item],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        identity_links=[],
        unresolved_notes=[],
    )


def test_targeted_repair_recovers_an_inventory_miss_from_local_text():
    citation = EvidenceCitation(
        block_id="b1", quote="Device B used a distinct fabrication route"
    )
    block = EvidenceBlock(
        block_id="b1",
        source="supplement",
        page=3,
        kind="paragraph",
        text="Device B used a distinct fabrication route.",
    )
    inventory = EvidenceInventory(
        items=[
            InventoryItem(
                item_id="inventory-b",
                kind="device_family",
                label="Device B",
                evidence=[citation],
            )
        ],
        exclusions=[],
    )
    study = empty_study()
    coverage = audit_inventory_coverage(inventory, study)

    class FakeClient:
        def complete(self, **request):
            assert request["kind"] == "targeted_study_repair"
            assert "PARSER TEXT AND TABLE EVIDENCE" in request["prompt"]
            return repair_with_family(family(citation))

    repaired, audit = run_targeted_repair(
        client=FakeClient(),
        study=study,
        blocks=[block],
        inventory=inventory,
        coverage=coverage,
        validation=validate_study(study, [block]),
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert audit.status == "accepted"
    assert [item.family_id for item in repaired.device_families] == ["family-b"]
    assert audit.after_quality["uncovered_inventory_items"] == 0


def test_targeted_repair_rejects_a_patch_that_breaks_grounding():
    valid = EvidenceCitation(block_id="b1", quote="Device B")
    invalid = EvidenceCitation(block_id="missing", quote="Device B")
    block = EvidenceBlock(
        block_id="b1", source="main", page=1, kind="paragraph", text="Device B"
    )
    study = empty_study().model_copy(update={"device_families": [family(valid)]})
    validation = validate_study(study, [block])
    validation["issues"].append(
        {"path": "$.device_families[0].label", "reason": "validation issue"}
    )

    class FakeClient:
        def complete(self, **request):
            return repair_with_family(family(invalid))

    repaired, audit = run_targeted_repair(
        client=FakeClient(),
        study=study,
        blocks=[block],
        inventory=None,
        coverage=None,
        validation=validation,
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert audit.status == "rejected"
    assert repaired == study
