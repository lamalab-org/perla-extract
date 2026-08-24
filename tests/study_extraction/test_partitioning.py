import pytest

from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.partitioning import plan_evidence_windows


def block(
    block_id: str, source: str, page: int, section: str, size: int
) -> EvidenceBlock:
    return EvidenceBlock(
        block_id=block_id,
        source=source,
        page=page,
        section_path=[section],
        kind="paragraph",
        text="x" * size,
    )


def test_long_supplement_has_exact_primary_coverage_and_main_context():
    blocks = [
        block("m1", "main", 1, "Methods", 100),
        block("m2", "main", 2, "Results", 100),
        *[block(f"s{i}", "supplement", i, f"S{i // 2}", 120) for i in range(1, 9)],
    ]
    plan = plan_evidence_windows(blocks, max_characters=500, max_context_characters=250)

    primary_ids = [
        item.block_id for window in plan.windows for item in window.primary_blocks
    ]
    assert primary_ids == [item.block_id for item in blocks]
    supplement_windows = [
        window for window in plan.windows if window.source == "supplement"
    ]
    assert len(supplement_windows) > 1
    assert all(
        [item.block_id for item in window.context_blocks] == ["m1", "m2"]
        for window in supplement_windows
    )
    assert all(window.character_count <= 500 for window in plan.windows)


def test_single_oversized_parser_block_is_kept_intact():
    plan = plan_evidence_windows(
        [block("large", "supplement", 1, "Table S1", 800)],
        max_characters=500,
        max_context_characters=0,
    )
    assert len(plan.windows) == 1
    assert plan.windows[0].primary_blocks[0].text == "x" * 800


def test_oversized_main_paper_is_not_repeated_as_supplement_context():
    blocks = [
        block("m1", "main", 1, "Methods", 300),
        block("s1", "supplement", 1, "Methods", 100),
    ]

    plan = plan_evidence_windows(
        blocks,
        max_characters=500,
        max_context_characters=250,
    )

    supplement = next(
        window for window in plan.windows if window.source == "supplement"
    )
    assert supplement.context_blocks == []


def test_duplicate_block_error_identifies_conflicting_ids():
    duplicate = block("same", "main", 1, "Methods", 100)

    with pytest.raises(ValueError, match=r"duplicates=\['same'\]"):
        plan_evidence_windows([duplicate, duplicate.model_copy()])
