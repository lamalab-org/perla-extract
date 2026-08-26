from perla_extract.study_extraction.claims import (
    ClaimLedger,
    ExperimentalObject,
    audit_claim_coverage,
)
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    StudyExtraction,
)
from perla_extract.study_extraction.repair import (
    RecordRemoval,
    RepairWorkItem,
    RepairWorklist,
    StudyRepair,
    _proposal_is_scoped,
    run_targeted_repair,
)
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
        removals=[],
        unresolved_notes=[],
    )


def test_targeted_repair_recovers_a_claim_miss_from_local_text():
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
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="object-b",
                role="device_design",
                scope="target",
                label="Device B",
                evidence=[citation],
            )
        ],
        claims=[],
    )
    study = empty_study()
    coverage = audit_claim_coverage(ledger, study)

    class FakeClient:
        def complete(self, **request):
            assert request["kind"] == "targeted_study_repair"
            assert "PARSER TEXT AND TABLE EVIDENCE" in request["prompt"]
            return repair_with_family(family(citation))

    repaired, audit = run_targeted_repair(
        client=FakeClient(),
        study=study,
        blocks=[block],
        ledger=ledger,
        coverage=coverage,
        validation=validate_study(study, [block]),
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert audit.status == "accepted"
    assert [item.family_id for item in repaired.device_families] == ["family-b"]
    assert audit.after_quality["semantic_issues"] == 0


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
        ledger=None,
        coverage=None,
        validation=validation,
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert audit.status == "rejected"
    assert repaired == study


def test_targeted_repair_can_remove_an_unclaimed_characterization_family():
    evidence = EvidenceCitation(block_id="b1", quote="film used for spectroscopy")
    block = EvidenceBlock(
        block_id="b1",
        source="supplement",
        page=1,
        kind="paragraph",
        text="A thin film used for spectroscopy was prepared.",
    )
    study = empty_study().model_copy(update={"device_families": [family(evidence)]})
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="spectroscopy-film",
                role="characterization_specimen",
                scope="context",
                label="thin spectroscopy film",
                evidence=[evidence],
            )
        ],
        claims=[],
    )
    coverage = audit_claim_coverage(ledger, study)

    class FakeClient:
        def complete(self, **request):
            return StudyRepair(
                device_families=[],
                individual_devices=[],
                performance_observations=[],
                population_statistics=[],
                stability_tests=[],
                removals=[
                    RecordRemoval(record_kind="device_family", record_id="family-b")
                ],
                unresolved_notes=[],
            )

    repaired, audit = run_targeted_repair(
        client=FakeClient(),
        study=study,
        blocks=[block],
        ledger=ledger,
        coverage=coverage,
        validation=validate_study(study, [block]),
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert audit.status == "accepted"
    assert repaired.device_families == []
    assert audit.after_quality["semantic_issues"] == 0


def test_targeted_repair_discards_additions_not_requested_by_the_worklist():
    evidence = EvidenceCitation(block_id="b1", quote="film used for spectroscopy")
    block = EvidenceBlock(
        block_id="b1",
        source="supplement",
        page=1,
        kind="paragraph",
        text="A thin film used for spectroscopy was prepared.",
    )
    other_block = EvidenceBlock(
        block_id="b2",
        source="main",
        page=2,
        kind="paragraph",
        text="The authors also measured another specimen.",
    )
    same_id_in_another_collection = IndividualDevice(
        device_id="family-b",
        family_id=None,
        label="Unrelated device with a colliding local ID",
        variant=None,
        champion_status="not_reported",
        selection_basis="not_reported",
        reported_properties=[],
        evidence=[EvidenceCitation(block_id="b2", quote="another specimen")],
    )
    study = empty_study().model_copy(
        update={
            "device_families": [family(evidence)],
            "individual_devices": [same_id_in_another_collection],
        }
    )
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="spectroscopy-film",
                role="characterization_specimen",
                scope="context",
                label="thin spectroscopy film",
                evidence=[evidence],
            )
        ],
        claims=[],
    )
    coverage = {
        "items": [
            {
                "status": "unclaimed",
                "record_kind": "device_family",
                "candidate_record_ids": ["family-b"],
                "evidence": [evidence.model_dump(mode="json")],
                "object_id": "spectroscopy-film",
                "label": "thin spectroscopy film",
            }
        ]
    }

    class FakeClient:
        def complete(self, **request):
            unrelated = family(evidence).model_copy(
                update={"family_id": "unrequested-family"}
            )
            return StudyRepair(
                device_families=[unrelated],
                individual_devices=[],
                performance_observations=[],
                population_statistics=[],
                stability_tests=[],
                removals=[
                    RecordRemoval(record_kind="device_family", record_id="family-b")
                ],
                unresolved_notes=["The cited film is not a photovoltaic device."],
            )

    repaired, audit = run_targeted_repair(
        client=FakeClient(),
        study=study,
        blocks=[block, other_block],
        ledger=ledger,
        coverage=coverage,
        validation=validate_study(study, [block, other_block]),
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert audit.status == "accepted"
    assert repaired.device_families == []
    assert repaired.individual_devices == [same_id_in_another_collection]
    assert repaired.unresolved_notes == [
        "The cited film is not a photovoltaic device."
    ]
    assert audit.discarded_record_counts["device_families"] == 1
    assert audit.reason == (
        "unrequested model additions were discarded before applying the patch"
    )


def test_repair_cannot_remove_and_replace_the_same_record():
    evidence = EvidenceCitation(block_id="b1", quote="Device B")
    proposed = repair_with_family(family(evidence)).model_copy(
        update={
            "removals": [
                RecordRemoval(record_kind="device_family", record_id="family-b")
            ]
        }
    )
    worklist = RepairWorklist(
        items=[
            RepairWorkItem(
                reason="unclaimed_record",
                record_kind="device_family",
                record_ids=["family-b"],
                block_ids=["b1"],
                detail="unsupported family",
            )
        ]
    )

    assert not _proposal_is_scoped(proposed, worklist)
