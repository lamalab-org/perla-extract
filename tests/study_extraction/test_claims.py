from perla_extract.study_extraction.claims import (
    ClaimLedger,
    ExperimentalObject,
    SourceClaim,
    assembly_blocks,
    assembly_spans,
    audit_claim_coverage,
    combine_ledgers,
    grounded_ledger,
)
from perla_extract.study_extraction.models import (
    AbsorberComponent,
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    MaterialConstituent,
    PaperMetadata,
    ReportedValue,
    StudyExtraction,
)


def citation(block_id: str, quote: str) -> EvidenceCitation:
    return EvidenceCitation(block_id=block_id, quote=quote)


def device_family(evidence: EvidenceCitation) -> DeviceFamily:
    return DeviceFamily(
        family_id="family-control",
        label="Control",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorbers=[],
        processing_steps=[],
        evidence=[evidence],
    )


def extraction(family: DeviceFamily | None = None) -> StudyExtraction:
    return StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[family] if family else [],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def test_assembly_keeps_claim_evidence_and_the_main_paper_header():
    blocks = [
        EvidenceBlock(
            block_id="title", source="main", page=1, kind="text", text="Paper title"
        ),
        EvidenceBlock(
            block_id="result",
            source="main",
            page=2,
            kind="text",
            text="The complete control device reached 20.1% efficiency.",
        ),
        EvidenceBlock(
            block_id="references",
            source="main",
            page=3,
            kind="text",
            text="References",
        ),
    ]
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="object-control",
                label="control device",
                role="device_design",
                scope="target",
                evidence=[citation("result", "complete control device")],
            )
        ],
        claims=[],
    )

    selected = assembly_blocks(blocks, ledger)

    assert [block.block_id for block in selected] == ["title", "result"]


def test_assembly_includes_only_a_bounded_local_neighborhood():
    block = EvidenceBlock(
        block_id="recipe",
        source="supplement",
        page=2,
        kind="table",
        text="First row\nThe solution was stirred\nIt was deposited immediately\nLast row",
    )
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="solution",
                label="solution",
                role="processing_arm",
                scope="target",
                evidence=[citation("recipe", "The solution was stirred")],
            )
        ],
        claims=[],
    )

    texts = [span.text for span in assembly_spans([block], ledger)]

    assert texts == [
        "First row",
        "The solution was stirred",
        "It was deposited immediately",
    ]


def test_window_ledgers_are_combined_without_colliding_local_ids():
    evidence = citation("result", "Device A reached 20%")
    part = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="device",
                label="Device A",
                role="device_design",
                scope="target",
                evidence=[evidence],
            )
        ],
        claims=[
            SourceClaim(
                claim_id="efficiency",
                kind="performance",
                label="efficiency",
                subject_object_ids=["device"],
                scope="target",
                raw_value="20%",
                shared_targets=[],
                evidence=[evidence],
            )
        ],
    )

    combined = combine_ledgers([("main", part), ("si", part)])

    assert [item.object_id for item in combined.objects] == [
        "main:device",
        "si:device",
    ]
    assert [item.subject_object_ids for item in combined.claims] == [
        ["main:device"],
        ["si:device"],
    ]


def test_characterization_object_does_not_require_or_justify_a_device_family():
    evidence = citation("result", "thin film used for spectroscopy")
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="thin-film",
                label="thin film",
                role="characterization_specimen",
                scope="context",
                evidence=[evidence],
            )
        ],
        claims=[],
    )

    empty_audit = audit_claim_coverage(ledger, extraction())
    overextracted_audit = audit_claim_coverage(
        ledger, extraction(device_family(evidence))
    )

    assert empty_audit["status"] == "complete"
    assert empty_audit["counts"]["context"] == 1
    assert overextracted_audit["counts"]["unclaimed_records"] == 1


def test_shared_quantity_requires_one_atomic_value_per_named_target():
    evidence = citation("recipe", "a 1.4 M PbI2, MAI, and DMSO solution")
    objects = [
        ExperimentalObject(
            object_id="solar-cell-design",
            label="complete solar cell",
            role="device_design",
            scope="target",
            evidence=[evidence],
        )
    ]
    claim = SourceClaim(
        claim_id="shared-recipe",
        kind="reported_quantity",
        label="solar-cell precursor concentrations",
        subject_object_ids=["solar-cell-design"],
        scope="target",
        raw_value="1.4 M",
        shared_targets=["PbI2", "MAI", "DMSO"],
        evidence=[evidence],
    )
    ledger = ClaimLedger(objects=objects, claims=[claim])
    incomplete = device_family(evidence).model_copy(
        update={
            "absorbers": [
                AbsorberComponent(
                    absorber_id="absorber",
                    layer_id=None,
                    label="MAPbI3",
                    formula=None,
                    constituents=[
                        MaterialConstituent(
                            name="PbI2",
                            role="precursor",
                            amount=ReportedValue(
                                name="PbI2 concentration",
                                raw_value="1.4 M",
                                value_number=1.4,
                                unit="M",
                                evidence=[evidence],
                            ),
                            evidence=[evidence],
                        )
                    ],
                    properties=[],
                    evidence=[evidence],
                )
            ]
        }
    )

    audit = audit_claim_coverage(ledger, extraction(incomplete))

    shared = next(
        item for item in audit["items"] if item.get("claim_id") == "shared-recipe"
    )
    assert shared["missing_shared_targets"] == ["MAI", "DMSO"]
    assert audit["counts"]["missing_shared_targets"] == 2
    assert audit["status"] == "needs_review"


def test_atomic_claim_requires_its_value_not_only_a_shared_citation():
    evidence = citation("recipe", "the precursor concentration was 11.4 M")
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="solar-cell-design",
                label="solar cell",
                role="device_design",
                scope="target",
                evidence=[evidence],
            )
        ],
        claims=[
            SourceClaim(
                claim_id="concentration",
                kind="reported_quantity",
                label="precursor concentration",
                subject_object_ids=["solar-cell-design"],
                scope="target",
                raw_value="1.4 M",
                shared_targets=[],
                evidence=[evidence],
            )
        ],
    )
    family_with_different_value = device_family(evidence).model_copy(
        update={
            "absorbers": [
                AbsorberComponent(
                    absorber_id="absorber",
                    layer_id=None,
                    label="absorber",
                    formula=None,
                    constituents=[
                        MaterialConstituent(
                            name="precursor",
                            role="precursor",
                            amount=ReportedValue(
                                name="precursor concentration",
                                raw_value="11.4 M",
                                value_number=11.4,
                                unit="M",
                                evidence=[evidence],
                            ),
                            evidence=[evidence],
                        )
                    ],
                    properties=[],
                    evidence=[evidence],
                )
            ]
        }
    )

    audit = audit_claim_coverage(ledger, extraction(family_with_different_value))

    claim = next(item for item in audit["items"] if item.get("claim_id"))
    assert claim["status"] == "possible_match"
    assert audit["issue_count"] == 1


def test_only_source_grounded_objects_and_claims_can_guide_assembly():
    blocks = [
        EvidenceBlock(
            block_id="result",
            source="main",
            page=1,
            kind="text",
            text="The control device reached 20.1% efficiency.",
        )
    ]
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="valid",
                label="control device",
                role="device_design",
                scope="target",
                evidence=[citation("result", "control device")],
            ),
            ExperimentalObject(
                object_id="invented",
                label="invented device",
                role="device_design",
                scope="target",
                evidence=[citation("result", "invented device")],
            ),
        ],
        claims=[],
    )

    grounded, audit = grounded_ledger(blocks, ledger)

    assert [item.object_id for item in grounded.objects] == ["valid"]
    assert audit["grounded_object_count"] == 1
    assert audit["rejected_count"] == 1
