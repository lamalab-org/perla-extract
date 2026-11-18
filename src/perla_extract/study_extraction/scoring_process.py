"""Identify fabrication operations and align their order without using outcomes.

Only explicit operation/method/target representations are interpreted. Temperatures,
durations and other measured conditions never decide which steps correspond.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import DeviceFamily, ProcessingStep, StudyExtraction
from .scoring_claims import scientific_text
from .scoring_vocabulary import material_name, property_name

# These conditions describe the identified operation, independently of an optional
# ingredients list. Concentrations and unfamiliar properties retain that list.
OPERATION_CONDITIONS = {
    "annealing": {
        "annealingtime": "duration",
        "annealingduration": "duration",
        "time": "duration",
        "duration": "duration",
        "annealingtemperature": "temperature",
        "temperature": "temperature",
    },
    "spin coating": {
        "spintime": "duration",
        "spinduration": "duration",
        "time": "duration",
        "duration": "duration",
        "spinspeed": "speed",
        "spincoatingspeed": "speed",
    },
}


def condition_name(name: str, operation: str) -> str:
    return OPERATION_CONDITIONS.get(operation, {}).get(
        property_name(name), property_name(name)
    )


@dataclass(frozen=True)
class ProcessDescription:
    operation: str
    method_index: int | None = None


def describe_process(
    step: ProcessingStep, targets: list[str], aliases: dict[str, str]
) -> ProcessDescription:
    """Resolve a small closed grammar, preserving unknown actions and qualifiers.

    A sonication method may be in the action or a dedicated method condition.
    A trailing substrate description is redundant only when it names the actual
    target. This does not equate generic cleaning with sonication, or strip an
    arbitrary target, atmosphere or treatment from prose.
    """

    raw = scientific_text(step.operation)
    raw = aliases.get(raw.casefold(), raw)
    if raw.casefold() == "annealed":
        raw = "annealing"
    operation = raw.casefold()
    cleaning = re.fullmatch(
        r"(sonication cleaning|ultrasonic cleaning|cleaning by sonication|cleaning)"
        r"(?: of (.+))?",
        raw,
        flags=re.IGNORECASE,
    )
    if cleaning:
        target = cleaning[2]
        if target is not None:
            target = re.sub(r" (substrates?|layers?)$", "", target, flags=re.IGNORECASE)
            if len(targets) != 1 or material_name(target) != material_name(targets[0]):
                return ProcessDescription(operation)
        operation = (
            "cleaning"
            if cleaning[1].casefold() == "cleaning"
            else "sonication cleaning"
        )
        methods = [
            i
            for i, value in enumerate(step.conditions)
            if re.sub(r"[\s_-]", "", value.name.casefold())
            in {"method", "cleaningmethod"}
            and scientific_text(value.raw_value).casefold()
            in {"sonication", "ultrasonication"}
            and value.unit is None
            and value.value_number is None
        ]
        if len(methods) == 1:
            return ProcessDescription("sonication cleaning", methods[0])
    return ProcessDescription(operation)


def _alignment_lengths(a: list[object], b: list[object]) -> list[list[int]]:
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            table[i + 1][j + 1] = max(
                table[i][j + 1], table[i + 1][j], table[i][j] + int(x == y)
            )
    return table


def ordered_overlap(left: list[object], right: list[object]) -> float:
    """Fraction of entries explained by their longest common ordered sequence."""
    return (
        2 * _alignment_lengths(left, right)[-1][-1] / (len(left) + len(right))
        if left or right
        else 1.0
    )


def ordered_alignment(
    left: list[object], right: list[object]
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Return forced and optional edges of all longest order-preserving matches.

    Dynamic programming intersects the edges shared by every optimal alignment.
    A second, reverse length table identifies all possible optimal edges. There
    is no outcome-dependent tie breaker and no enumeration of alignments.
    """

    prefix = _alignment_lengths(left, right)
    suffix = _alignment_lengths(left[::-1], right[::-1])
    previous: list[frozenset[tuple[int, int]]] = [frozenset()] * (len(right) + 1)
    for i, x in enumerate(left):
        current: list[frozenset[tuple[int, int]]] = [frozenset()]
        for j, y in enumerate(right):
            best = prefix[i + 1][j + 1]
            options = []
            if prefix[i][j + 1] == best:
                options.append(previous[j + 1])
            if prefix[i + 1][j] == best:
                options.append(current[j])
            if x == y and prefix[i][j] + 1 == best:
                options.append(previous[j] | {(i, j)})
            current.append(frozenset.intersection(*options))
        previous = current
    forced = set(previous[-1])
    possible = {
        (i, j)
        for i, x in enumerate(left)
        for j, y in enumerate(right)
        if x == y
        and prefix[i][j] + 1 + suffix[len(left) - i - 1][len(right) - j - 1]
        == prefix[-1][-1]
    }
    return forced, possible - forced


@dataclass
class ProcessAlignment:
    # None is an explicitly unmatched step. An absent key uses the recorded
    # sequence/operation/target scope for partial orders or shared positions.
    references: list[dict[str, str | None]] = field(default_factory=lambda: [{}, {}])
    ambiguous: list[set[str]] = field(default_factory=lambda: [set(), set()])


def align_processes(
    truth: StudyExtraction,
    prediction: StudyExtraction,
    family_pairs: list[tuple[str, str]],
    aliases: dict[str, str],
    layer_references: list[dict[str, str | None]] | None = None,
) -> ProcessAlignment:
    """Align fully ordered recipes; retain explicit scopes for partial orders."""

    result = ProcessAlignment()
    families = [
        {f.family_id: (i, f) for i, f in enumerate(study.device_families)}
        for study in (truth, prediction)
    ]

    def ordered(family: DeviceFamily) -> bool:
        positions = [s.sequence for s in family.processing_steps]
        return None not in positions and len(set(positions)) == len(positions)

    def entries(
        index: int, family: DeviceFamily, side: int
    ) -> list[tuple[str, object]]:
        layers = {layer.layer_id: layer for layer in family.layers}
        layer_paths = {
            layer.layer_id: f"/device_families/{index}/layers/{i}"
            for i, layer in enumerate(family.layers)
        }
        links = layer_references[side] if layer_references is not None else {}

        def target(identifier: str) -> object:
            path = layer_paths.get(identifier)
            if path in links:
                return links[path] or f"{side}:unmatched:{path}"
            return (
                (
                    layers[identifier].sequence,
                    material_name(layers[identifier].material),
                )
                if identifier in layers
                else (None, f"{side}:unmatched:{identifier}")
            )

        rows: list[tuple[str, object]] = []
        for i, step in sorted(
            enumerate(family.processing_steps), key=lambda pair: pair[1].sequence or 0
        ):
            path = f"/device_families/{index}/processing_steps/{i}"
            target_names = [
                layers[t].material for t in step.target_layer_ids if t in layers
            ]
            targets = sorted(
                [target(t) for t in step.target_layer_ids],
                key=repr,
            )
            operation = describe_process(step, target_names, aliases).operation
            rows.append((path, (operation, tuple(targets))))
        return rows

    for a_id, b_id in family_pairs:
        a_index, a = families[0][a_id]
        b_index, b = families[1][b_id]
        if not ordered(a) or not ordered(b):
            continue
        left, right = entries(a_index, a, 0), entries(b_index, b, 1)
        for side, rows in enumerate((left, right)):
            result.references[side].update((path, None) for path, _ in rows)
        forced, optional = ordered_alignment(
            [v for _, v in left], [v for _, v in right]
        )
        for i, j in forced:
            result.references[0][left[i][0]] = left[i][0]
            result.references[1][right[j][0]] = left[i][0]
        result.ambiguous[0].update(left[i][0] for i, _ in optional)
        result.ambiguous[1].update(right[j][0] for _, j in optional)
    return result
