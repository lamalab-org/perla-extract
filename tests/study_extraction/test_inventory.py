from perla_extract.study_extraction.inventory import (
    EvidenceExclusion,
    EvidenceInventory,
    InventoryItem,
    audit_inventory_coverage,
    grounded_inventory_items,
    routed_blocks,
)
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    PaperMetadata,
    StudyExtraction,
)


def extraction() -> StudyExtraction:
    citation = EvidenceCitation(block_id="result", quote="control device")
    return StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[
            DeviceFamily(
                family_id="f1",
                label="control",
                variant=None,
                architecture=None,
                polarity="not_reported",
                full_stack_raw=None,
                layers=[],
                absorber_formula=None,
                absorber_properties=[],
                absorber_constituents=[],
                processing_steps=[],
                evidence=[citation],
            )
        ],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def test_routing_fails_open_and_candidate_evidence_overrides_exclusion():
    blocks = [
        EvidenceBlock(
            block_id="result", source="main", page=1, kind="text", text="result"
        ),
        EvidenceBlock(
            block_id="references",
            source="main",
            page=2,
            kind="text",
            text="references",
        ),
    ]
    inventory = EvidenceInventory(
        items=[
            InventoryItem(
                item_id="i1",
                kind="device_family",
                label="control",
                evidence=[EvidenceCitation(block_id="result", quote="control device")],
            )
        ],
        exclusions=[
            EvidenceExclusion(
                evidence=EvidenceCitation(block_id="result", quote="result"),
                reason="mistake",
            ),
            EvidenceExclusion(
                evidence=EvidenceCitation(block_id="references", quote="references"),
                reason="bibliography",
            ),
            EvidenceExclusion(
                evidence=EvidenceCitation(block_id="invented", quote="unknown"),
                reason="unknown",
            ),
        ],
    )

    selected, audit = routed_blocks(blocks, inventory)

    assert [block.block_id for block in selected] == ["result"]
    assert audit["protected_block_ids"] == ["result"]
    assert audit["invalid_exclusion_block_ids"] == ["invented"]


def test_independent_inventory_reports_exact_and_unmatched_candidates():
    inventory = EvidenceInventory(
        items=[
            InventoryItem(
                item_id="covered",
                kind="device_family",
                label="control",
                evidence=[EvidenceCitation(block_id="result", quote="control device")],
            ),
            InventoryItem(
                item_id="missing",
                kind="stability_test",
                label="aged device",
                evidence=[EvidenceCitation(block_id="aging", quote="aged for 1000 h")],
            ),
        ],
        exclusions=[],
    )

    audit = audit_inventory_coverage(inventory, extraction())

    assert audit["counts"] == {"covered": 1, "possible_match": 0, "unmatched": 1}
    assert audit["status"] == "needs_review"


def test_only_source_grounded_inventory_candidates_can_guide_extraction():
    blocks = [
        EvidenceBlock(
            block_id="result",
            source="main",
            page=1,
            kind="text",
            text="The control device reached 20.1% efficiency.",
        )
    ]
    inventory = EvidenceInventory(
        items=[
            InventoryItem(
                item_id="valid",
                kind="device_family",
                label="control",
                evidence=[
                    EvidenceCitation(block_id="result", quote="control device")
                ],
            ),
            InventoryItem(
                item_id="invented",
                kind="device_family",
                label="invented variant",
                evidence=[
                    EvidenceCitation(block_id="result", quote="invented variant")
                ],
            ),
        ],
        exclusions=[],
    )

    items, audit = grounded_inventory_items(blocks, inventory)

    assert [item.item_id for item in items] == ["valid"]
    assert audit["grounded_item_count"] == 1
    assert audit["rejected_item_count"] == 1
