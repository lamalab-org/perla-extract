"""Align explicitly ordered layers without letting renumbering change ownership.

The material sequence selects correspondences. Roles, forms and composition are
scored afterward; their completeness cannot resolve repeated material layers.
Only pairs shared by every longest ordered alignment establish a layer link.
"""

from dataclasses import dataclass, field

from .models import DeviceFamily, Layer, StudyExtraction
from .scoring_claims import scientific_text
from .scoring_process import ordered_alignment, ordered_overlap
from .scoring_vocabulary import material_name


def ordered_layers(family: DeviceFamily) -> list[tuple[int, Layer]] | None:
    positions = [layer.sequence for layer in family.layers]
    if not positions or None in positions or len(set(positions)) != len(positions):
        return None
    return sorted(enumerate(family.layers), key=lambda pair: pair[1].sequence or 0)


def stack_similarity(left: DeviceFamily, right: DeviceFamily) -> float | None:
    """Return (2L+V)/(n+m+1): ordered material overlap plus exact variant support.

    A partial or absent order uses the existing conservative feature fallback.
    V is one only for equal, nonempty variant labels. Their length adds no weight.
    Missing and different labels both add zero; an omitted label cannot be favored
    over a conflicting label. Uninterpreted paraphrases cannot resolve a tie.
    """
    a, b = ordered_layers(left), ordered_layers(right)
    if a is None or b is None:
        return None
    overlap = ordered_overlap(
        [material_name(layer.material) for _, layer in a],
        [material_name(layer.material) for _, layer in b],
    )
    same_variant = bool(
        left.variant
        and right.variant
        and scientific_text(left.variant) == scientific_text(right.variant)
    )
    entries = len(a) + len(b)
    return (overlap * entries + int(same_variant)) / (entries + 1)


@dataclass
class LayerAlignment:
    references: list[dict[str, str | None]] = field(default_factory=lambda: [{}, {}])
    ambiguous: list[set[str]] = field(default_factory=lambda: [set(), set()])


def align_layers(
    truth: StudyExtraction,
    prediction: StudyExtraction,
    family_pairs: list[tuple[str, str]],
) -> LayerAlignment:
    """Share forced layer links with material facts, absorber links and recipes."""
    result = LayerAlignment()
    families = [
        {f.family_id: (i, f) for i, f in enumerate(study.device_families)}
        for study in (truth, prediction)
    ]
    for left_id, right_id in family_pairs:
        a_index, a = families[0][left_id]
        b_index, b = families[1][right_id]
        a_layers, b_layers = ordered_layers(a), ordered_layers(b)
        if a_layers is None or b_layers is None:
            continue
        paths = [
            [f"/device_families/{index}/layers/{i}" for i, _ in entries]
            for index, entries in ((a_index, a_layers), (b_index, b_layers))
        ]
        for side in range(2):
            result.references[side].update((path, None) for path in paths[side])
        forced, optional = ordered_alignment(
            [material_name(layer.material) for _, layer in a_layers],
            [material_name(layer.material) for _, layer in b_layers],
        )
        for i, j in forced:
            result.references[0][paths[0][i]] = paths[0][i]
            result.references[1][paths[1][j]] = paths[0][i]
        result.ambiguous[0].update(paths[0][i] for i, _ in optional)
        result.ambiguous[1].update(paths[1][j] for _, j in optional)
    return result
