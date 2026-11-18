"""Plan bounded model calls without losing evidence from long supplements.

Partitioning is based on parser-produced blocks, pages, and section paths.  It
does not search for domain terms.  Every block is primary evidence in exactly
one window; a small main paper may additionally be repeated as read-only context
for supplement windows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence

from pydantic import Field, model_validator

from .models import EvidenceBlock, Identifier, StrictModel


class EvidenceWindow(StrictModel):
    """Describe primary evidence and optional read-only context for one call."""

    window_id: Identifier
    source: str
    primary_blocks: list[EvidenceBlock]
    context_blocks: list[EvidenceBlock]

    @property
    def character_count(self) -> int:
        """Count source-text characters, excluding instructions and serialization."""

        return sum(block.character_count for block in self.request_blocks)

    @property
    def request_blocks(self) -> list[EvidenceBlock]:
        """Return context followed by primary evidence in stable source order."""

        return [*self.context_blocks, *self.primary_blocks]


class EvidenceWindowPlan(StrictModel):
    """Record the complete and auditable partition of source evidence."""

    block_ids: list[Identifier]
    windows: list[EvidenceWindow]
    max_characters: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_coverage(self) -> EvidenceWindowPlan:
        """Check primary coverage and the preliminary source-text packing limit.

        An indivisible oversized block may survive this structural plan. The final
        serialized-request check must either fit it or reject it before a model call.
        """

        expected = set(self.block_ids)
        if len(expected) != len(self.block_ids):
            raise ValueError("block_ids must be unique")
        primary = [
            block.block_id for window in self.windows for block in window.primary_blocks
        ]
        if len(primary) != len(set(primary)):
            raise ValueError("a block is primary evidence in more than one window")
        if set(primary) != expected:
            missing = sorted(expected - set(primary))
            unknown = sorted(set(primary) - expected)
            raise ValueError(
                f"incomplete evidence coverage: missing={missing}, unknown={unknown}"
            )
        oversized = [
            window.window_id
            for window in self.windows
            if window.character_count > self.max_characters
            and not (
                len(window.primary_blocks) == 1
                and window.primary_blocks[0].character_count > self.max_characters
            )
        ]
        if oversized:
            raise ValueError(f"windows exceed max_characters: {oversized}")
        return self


def _section_key(block: EvidenceBlock) -> tuple[str, str]:
    """Group adjacent blocks by source and top-level parser section."""

    section = block.section_path[0] if block.section_path else f"page:{block.page}"
    return block.source, section


def _group_contiguous_sections(
    blocks: Sequence[EvidenceBlock],
) -> list[list[EvidenceBlock]]:
    """Keep consecutive section blocks together before enforcing the size cap."""

    groups: list[list[EvidenceBlock]] = []
    for block in blocks:
        if not groups or _section_key(groups[-1][-1]) != _section_key(block):
            groups.append([])
        groups[-1].append(block)
    return groups


def _split_oversized_group(
    group: Sequence[EvidenceBlock], budget: int
) -> list[list[EvidenceBlock]]:
    """Split an oversized section at block boundaries, preferring page boundaries."""

    page_groups: list[list[EvidenceBlock]] = []
    for block in group:
        if not page_groups or page_groups[-1][-1].page != block.page:
            page_groups.append([])
        page_groups[-1].append(block)

    units: list[list[EvidenceBlock]] = []
    for page_group in page_groups:
        if sum(item.character_count for item in page_group) <= budget:
            units.append(page_group)
        else:
            units.extend([[item] for item in page_group])
    return units


def _pack_structural_groups(
    groups: Iterable[Sequence[EvidenceBlock]], budget: int
) -> list[list[EvidenceBlock]]:
    """Greedily pack ordered structural groups while retaining every block."""

    packed: list[list[EvidenceBlock]] = []
    current: list[EvidenceBlock] = []
    current_size = 0
    for group in groups:
        group_size = sum(block.character_count for block in group)
        if group_size <= budget:
            if current and current_size + group_size > budget:
                packed.append(current)
                current, current_size = [], 0
            current.extend(group)
            current_size += group_size
            continue

        if current:
            packed.append(current)
            current, current_size = [], 0
        for unit in _split_oversized_group(group, budget):
            unit_size = sum(block.character_count for block in unit)
            if current and current_size + unit_size > budget:
                packed.append(current)
                current, current_size = [], 0
            current.extend(unit)
            current_size += unit_size
            if unit_size > budget:
                packed.append(current)
                current, current_size = [], 0
    if current:
        packed.append(current)
    return packed


def plan_evidence_windows(
    blocks: Sequence[EvidenceBlock],
    *,
    max_characters: int = 60_000,
    main_source: str = "main",
    max_context_characters: int = 20_000,
) -> EvidenceWindowPlan:
    """Create an initial section-aware source partition for a paper and its SI.

    The entire main paper is repeated as context only when it fits the context
    allowance.  Context does not count as a second primary occurrence, so an
    extractor can be instructed to propose records only from ``primary_blocks``.
    A single parser block larger than the cap is retained intact and placed in a
    window by itself instead of being silently truncated.
    """

    if max_characters <= 0:
        raise ValueError("max_characters must be positive")
    if not 0 <= max_context_characters < max_characters:
        raise ValueError("max_context_characters must be in [0, max_characters)")
    block_id_counts = Counter(block.block_id for block in blocks)
    duplicate_block_ids = sorted(
        block_id for block_id, count in block_id_counts.items() if count > 1
    )
    if duplicate_block_ids:
        raise ValueError(
            f"block_id values must be unique; duplicates={duplicate_block_ids}"
        )

    main = [block for block in blocks if block.source == main_source]
    context = (
        main
        if main
        and sum(block.character_count for block in main) <= max_context_characters
        else []
    )

    by_source: dict[str, list[EvidenceBlock]] = {}
    source_order: list[str] = []
    for block in blocks:
        if block.source not in by_source:
            by_source[block.source] = []
            source_order.append(block.source)
        by_source[block.source].append(block)

    windows: list[EvidenceWindow] = []
    for source in source_order:
        source_context = context if source != main_source else []
        budget = max_characters - sum(item.character_count for item in source_context)
        primary_groups = _pack_structural_groups(
            _group_contiguous_sections(by_source[source]), budget
        )
        for index, primary in enumerate(primary_groups, start=1):
            primary_size = sum(item.character_count for item in primary)
            context_size = sum(item.character_count for item in source_context)
            window_context = (
                source_context if primary_size + context_size <= max_characters else []
            )
            windows.append(
                EvidenceWindow(
                    window_id=f"{source}-{index:04d}",
                    source=source,
                    primary_blocks=primary,
                    context_blocks=window_context,
                )
            )

    return EvidenceWindowPlan(
        block_ids=[block.block_id for block in blocks],
        windows=windows,
        max_characters=max_characters,
    )


def fit_request_windows(
    windows: Sequence[EvidenceWindow],
    *,
    max_input_tokens: int,
    estimate: Callable[[list[EvidenceBlock], list[EvidenceBlock]], int],
) -> list[EvidenceWindow]:
    """Fit complete serialized requests while preserving every primary block.

    Structural packing is only a first approximation. The caller measures the final
    instructions, citation schema, source serialization, and system message here.
    Optional repeated context is removed before splitting primary blocks. An
    indivisible block that cannot fit raises an error instead of truncating evidence
    or silently returning an over-budget request.
    """

    result: list[EvidenceWindow] = []

    def fit(window: EvidenceWindow) -> None:
        primary, context = window.primary_blocks, window.context_blocks
        size = estimate(primary, context)
        if size > max_input_tokens and context:
            context = []
            size = estimate(primary, context)
        if size <= max_input_tokens:
            result.append(window.model_copy(update={"context_blocks": context}))
            return
        if len(primary) == 1:
            raise ValueError(
                f"source block {primary[0].block_id!r} needs an estimated {size} input "
                f"tokens, exceeding the reading limit {max_input_tokens}; use a larger "
                "declared context/input budget or a parser that yields smaller blocks"
            )
        middle = len(primary) // 2
        for suffix, subset in (("a", primary[:middle]), ("b", primary[middle:])):
            fit(
                window.model_copy(
                    update={
                        "window_id": f"{window.window_id}-{suffix}",
                        "primary_blocks": subset,
                        "context_blocks": context,
                    }
                )
            )

    for window in windows:
        fit(window)
    return result
