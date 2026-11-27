import pytest

from perla_extract.study_extraction.claims import (
    ClaimLedger,
    ExperimentalObject,
    audit_claim_coverage,
)
from perla_extract.study_extraction.correction_checks import (
    _select_refinement_candidate,
)
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    ReportedValue,
    StudyExtraction,
)
from perla_extract.study_extraction.repair import (
    RecordRemoval,
    RepairWorkItem,
    RepairWorklist,
    StudyRepair,
    _monotonic_repair_subset,
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


@pytest.mark.parametrize("replacement", [False, True])
def test_repair_cannot_improve_issue_counts_by_erasing_source_values(replacement):
    block = EvidenceBlock(
        block_id="b1",
        source="main",
        page=1,
        kind="text",
        text="Device B was measured with a delay of 40 ms.",
    )
    citation = EvidenceCitation(block_id="b1", quote=block.text)
    device = IndividualDevice(
        device_id="device-b",
        family_id=None,
        label="Device B",
        variant=None,
        champion_status="not_reported",
        selection_basis="not_reported",
        reported_properties=[
            ReportedValue(
                name="delay",
                raw_value=raw,
                value_number=number,
                unit="ms",
                evidence=[citation],
            )
            for raw, number in [("40 ms", 40), ("80 ms", 80)]
        ],
        evidence=[citation],
    )
    study = empty_study().model_copy(update={"individual_devices": [device]})
    # An incomplete reader found no target objects. Absence from its notes is
    # not evidence that the explicitly reported measurement should be erased.
    ledger = ClaimLedger(objects=[], claims=[])
    proposed = StudyRepair(
        device_families=[],
        individual_devices=[device.model_copy(update={"reported_properties": []})]
        if replacement
        else [],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        removals=[]
        if replacement
        else [RecordRemoval(record_kind="individual_device", record_id="device-b")],
        unresolved_notes=[],
    )

    class FakeClient:
        def complete(self, **request):
            return proposed

    repaired, audit = run_targeted_repair(
        client=FakeClient(),
        study=study,
        blocks=[block],
        ledger=ledger,
        coverage=audit_claim_coverage(ledger, study),
        validation=validate_study(study, [block]),
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )
    assert repaired == study
    assert audit.status == "rejected"
    assert (
        audit.after_quality["validation_issues"]
        < audit.before_quality["validation_issues"]
    )
    assert audit.after_quality["lost_verified_values"] == 1
    assert audit.reason == "proposed patch would discard source-supported values"


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
    assert repaired.unresolved_notes == []  # Notes describe the unfiltered proposal.
    assert audit.discarded_record_counts["device_families"] == 1
    assert audit.reason == (
        "unrequested model additions were discarded before applying the patch"
    )


def test_removing_unlinked_records_cannot_compensate_for_losing_a_target():
    block = EvidenceBlock(
        block_id="b1", source="main", page=1, kind="text", text="Device B was tested."
    )
    evidence = EvidenceCitation(block_id="b1", quote=block.text)
    target = family(evidence)
    extras = [
        family(EvidenceCitation(block_id="missing", quote="Unlinked")).model_copy(
            update={"family_id": f"extra-{i}", "label": f"Extra {i}"}
        )
        for i in range(2)
    ]
    study = empty_study().model_copy(update={"device_families": [target, *extras]})
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="b",
                label="Device B",
                role="device_design",
                scope="target",
                evidence=[evidence],
            )
        ],
        claims=[],
    )
    selected, audit = _select_refinement_candidate(
        study, empty_study(), [block], ledger
    )
    assert selected == study
    before, after = audit["draft_quality"], audit["refinement_quality"]
    assert after["semantic_issues"] < before["semantic_issues"]
    assert after["missing_claims"] > before["missing_claims"]


def test_equal_values_for_distinct_devices_are_not_duplicates():
    block = EvidenceBlock(
        block_id="b1",
        source="main",
        page=1,
        kind="text",
        text="Device A and Device B were both measured at 40 ms.",
    )
    evidence = EvidenceCitation(block_id="b1", quote=block.text)
    devices = [
        IndividualDevice(
            device_id=name,
            family_id=None,
            label=name,
            variant=None,
            champion_status="not_reported",
            selection_basis="not_reported",
            reported_properties=[
                ReportedValue(
                    name="delay",
                    raw_value="40 ms",
                    value_number=40,
                    unit="ms",
                    evidence=[evidence],
                )
            ],
            evidence=[evidence],
        )
        for name in ("Device A", "Device B")
    ]
    study = empty_study().model_copy(update={"individual_devices": devices})
    dropped = study.model_copy(update={"individual_devices": devices[:1]})
    selected, audit = _select_refinement_candidate(study, dropped, [block], None)
    assert selected == study
    assert audit["refinement_quality"]["lost_verified_values"] == 1


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


def test_repair_salvages_only_independently_quality_improving_records():
    block = EvidenceBlock(
        block_id="b1", source="main", page=1, kind="paragraph", text="Device B"
    )
    valid = EvidenceCitation(block_id="b1", quote="Device B")
    invalid = EvidenceCitation(block_id="missing", quote="Device B")
    study = empty_study().model_copy(update={"device_families": [family(invalid)]})
    proposed = StudyRepair(
        device_families=[
            family(valid),
            family(invalid).model_copy(update={"family_id": "unrelated-c"}),
            family(invalid).model_copy(update={"family_id": "unrelated-d"}),
        ],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        removals=[],
        unresolved_notes=[],
    )

    repaired, quality, counts = _monotonic_repair_subset(
        study,
        proposed,
        [block],
        None,
        {
            "validation_issues": 1,
            "reported_values": 0,
            "source_verified_values": 0,
            "semantic_issues": 0,
            "missing_claims": 0,
            "lost_verified_values": 0,
        },
    )

    assert [item.family_id for item in repaired.device_families] == ["family-b"]
    assert repaired.device_families[0].evidence == [valid]
    assert quality["validation_issues"] == 0
    assert counts["device_families"] == 1


def test_repair_provider_schema_excludes_unrequested_scientific_record_types():
    from perla_extract.study_extraction.repair import (
        RepairWorkItem,
        RepairWorklist,
        StudyRepair,
        _decode_repair,
        repair_request_schema,
    )
    from perla_extract.study_extraction.spans import build_evidence_spans

    block = EvidenceBlock(
        block_id="source",
        source="main",
        page=1,
        kind="text",
        text="The device was measured.",
    )
    spans = build_evidence_spans([block])
    worklist = RepairWorklist(
        items=[
            RepairWorkItem(
                reason="validation_issue",
                record_kind="individual_device",
                record_ids=["device-a"],
                block_ids=["source"],
                detail="Repair the invalid value.",
            )
        ]
    )
    schema = repair_request_schema(worklist, spans)
    assert set(schema["properties"]) == {"individual_devices", "unresolved_notes"}
    assert "DeviceFamily" not in schema["$defs"]
    assert "StabilityTest" not in schema["$defs"]
    assert schema["$defs"]["IndividualDevice"]["properties"]["device_id"]["enum"] == [
        "device-a"
    ]
    result = StudyRepair.model_validate(
        _decode_repair({"individual_devices": [], "unresolved_notes": []}, spans)
    )
    assert result.device_families == result.removals == []
