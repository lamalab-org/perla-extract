"""Render a study extraction as cited prose for a quick side-by-side comparison.

Both candidates of a simplified A/B comparison pass through exactly this renderer, so
any difference a reviewer sees comes from the extracted content, never from wording
or layout. The renderer is deterministic and blind:

* every section appears in the same order, with a fixed sentence when it is empty;
* records are ordered by their scientific content, not by the order an extractor or
  a reviewer happened to write them;
* publication metadata comes from the comparison, never from either candidate, so a
  missing title on one side cannot reveal which side is which;
* record identifiers, free-text notes, and run metadata are never rendered; and
* citations become numbered references to a page and a quote, so they look the
  same whether they came from a parser block of the reviewed document or of a run.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from perla_extract.study_extraction.models import EvidenceBlock, StudyExtraction

SectionKey = Literal["study", "devices", "results", "stability"]
SECTIONS: list[tuple[SectionKey, str]] = [
    ("study", "Study"),
    ("devices", "Investigated devices"),
    ("results", "Results of characterization"),
    ("stability", "Stability"),
]
EMPTY: dict[SectionKey, str] = {
    "study": "",
    "devices": "No device families are reported.",
    "results": "No characterization results are reported.",
    "stability": "No stability tests are reported.",
}

LABELS = {
    "transparent_electrode": "transparent electrode",
    "hole_transport_layer": "hole-transport layer",
    "electron_transport_layer": "electron-transport layer",
    "back_electrode": "back electrode",
    "recombination_layer": "recombination layer",
    "buffer_layer": "buffer layer",
    "jv_scan": "J–V scan",
    "stabilized_power_output": "stabilized power output",
    "certified_measurement": "certified measurement",
    "eqe_integrated_current": "EQE-integrated current",
    "before_device_completion": "before device completion",
    "completed_device": "on the completed device",
    "standard_deviation": "standard deviation",
    "standard_error": "standard error",
    "confidence_interval": "confidence interval",
}
STATISTICS = {
    "mean": "a mean",
    "median": "a median",
    "minimum": "a minimum",
    "maximum": "a maximum",
    "standard_deviation": "a standard deviation of the",
    "range": "a range of the",
    "distribution": "a distribution of the",
}
NUMBER_WORDS = [
    "no",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
]


class PaperInfo(BaseModel):
    """Publication metadata shared by both candidates of one comparison."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=800)
    authors: list[str] = Field(default_factory=list, max_length=200)
    journal: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=1900, le=2100)
    doi: str | None = Field(default=None, max_length=300)


class Segment(BaseModel):
    """A run of text followed by the references that support it."""

    model_config = ConfigDict(extra="forbid")
    text: str
    citations: list[int] = []


class Paragraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[Segment]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: SectionKey
    title: str
    paragraphs: list[Paragraph]


class Reference(BaseModel):
    """A numbered citation; source and page are None when the block is unknown."""

    model_config = ConfigDict(extra="forbid")
    number: int
    source: str | None
    page: int | None
    quote: str


class ProseDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[Section]
    references: list[Reference]


def _label(value: object) -> str:
    text = str(value)
    return LABELS.get(text, text.replace("_", " "))


def _reported(value: object) -> bool:
    return bool(value) and value not in {"not_reported", "other"}


def _join(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _count(number: int, singular: str, plural: str | None = None) -> str:
    word = NUMBER_WORDS[number] if number < len(NUMBER_WORDS) else str(number)
    return f"{word} {singular if number == 1 else plural or singular + 's'}"


def _quantity(value: dict[str, Any]) -> str:
    raw = str(value.get("raw_value") or "").strip()
    unit = str(value.get("unit") or "").strip()
    if unit and not raw.endswith(unit):
        raw = f"{raw}{'' if unit == '%' else ' '}{unit}"
    return raw


def _compact(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", normalized).strip()


def _sort_text(*parts: object) -> tuple[str, ...]:
    return tuple(_compact(str(part or "")).casefold() for part in parts)


def _sequence_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the reported sequence; unsequenced items follow in their given order."""

    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            pair[1].get("sequence") is None,
            pair[1].get("sequence") or 0,
            pair[0],
        )
    )
    return [item for _, item in indexed]


def _surname(author: str) -> str:
    author = _compact(author)
    if "," in author:
        return author.split(",", 1)[0].strip()
    return author.split(" ")[-1] if author else ""


def authors_phrase(authors: list[str]) -> str:
    """ "Zhao", "Zhao and Li", or "Zhao et al."; "The authors" when none are known."""

    names = [_surname(author) for author in authors if _surname(author)]
    if not names:
        return "The authors"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} et al."


class _Writer:
    """Number references by first use across one candidate's prose."""

    def __init__(self, blocks: list[EvidenceBlock]) -> None:
        self.blocks = {block.block_id: block for block in blocks}
        self.references: list[Reference] = []
        self.numbers: dict[tuple[object, ...], int] = {}

    def cite(self, evidence: list[dict[str, Any]] | None) -> list[int]:
        numbers: list[int] = []
        for citation in evidence or []:
            quote = _compact(str(citation.get("quote") or ""))
            if not quote:
                continue
            block = self.blocks.get(str(citation.get("block_id")))
            key: tuple[object, ...] = (
                (block.source, block.page, quote)
                if block
                else ("unknown", str(citation.get("block_id")), quote)
            )
            if key not in self.numbers:
                self.numbers[key] = len(self.references) + 1
                self.references.append(
                    Reference(
                        number=self.numbers[key],
                        source=block.source if block else None,
                        page=block.page if block else None,
                        quote=quote,
                    )
                )
            if self.numbers[key] not in numbers:
                numbers.append(self.numbers[key])
        return numbers


class _Paragraph:
    def __init__(self, writer: _Writer) -> None:
        self.writer = writer
        self.segments: list[Segment] = []

    def mark(self) -> int:
        """Remember where a record starts, for ``text(..., since=mark)``."""

        open_segment = bool(self.segments) and not self.segments[-1].citations
        return len(self.segments) - 1 if open_segment else len(self.segments)

    def text(
        self,
        text: str,
        evidence: list[dict[str, Any]] | None = None,
        *,
        since: int | None = None,
    ) -> None:
        """Append text and its citations.

        With ``since``, numbers already cited after that mark are not repeated, so a
        record's own evidence does not echo the evidence of its values.
        """

        citations = self.writer.cite(evidence)
        if since is not None:
            cited = {n for segment in self.segments[since:] for n in segment.citations}
            citations = [n for n in citations if n not in cited]
        if not text and self.segments:
            last = self.segments[-1]
            last.citations += [n for n in citations if n not in last.citations]
            return
        if self.segments and not self.segments[-1].citations:
            self.segments[-1].text += text
            self.segments[-1].citations = citations
        else:
            self.segments.append(Segment(text=text, citations=citations))

    def values(
        self, values: list[dict[str, Any]], *, end: str = "", prefix: str = ""
    ) -> None:
        """Write "a of 1 [1], b of 2 [2] and c of 3 [3]" with a citation per value."""

        values = [value for value in values if value]
        for index, value in enumerate(values):
            if index == 0:
                separator = prefix
            elif index == len(values) - 1:
                separator = " and "
            else:
                separator = ", "
            self.text(
                f"{separator}{value.get('name')} of {_quantity(value)}",
                value.get("evidence"),
            )
        if end:
            self.text(end)

    def build(self) -> Paragraph | None:
        segments = [segment for segment in self.segments if segment.text]
        if not segments:
            return None
        segments[0].text = segments[0].text.lstrip()
        segments[-1].text = segments[-1].text.rstrip()
        for segment in segments:
            segment.citations.sort()
        return Paragraph(segments=segments)


def _family_key(family: dict[str, Any]) -> tuple[str, ...]:
    layers = _sequence_order(family.get("layers") or [])
    formulas = sorted(
        str((absorber.get("formula") or {}).get("raw_value") or "")
        for absorber in family.get("absorbers") or []
    )
    return _sort_text(
        " | ".join(str(layer.get("material")) for layer in layers),
        " ".join(formulas),
        family.get("polarity"),
        family.get("label"),
        family.get("variant"),
    )


def _constituents(paragraph: _Paragraph, constituents: list[dict[str, Any]]) -> None:
    for index, constituent in enumerate(constituents):
        if index == 0:
            separator = " "
        elif index == len(constituents) - 1:
            separator = " and "
        else:
            separator = ", "
        name = str(constituent.get("name"))
        text = f"{name} as {constituent['role']}" if constituent.get("role") else name
        amount = constituent.get("amount")
        evidence = list(constituent.get("evidence") or [])
        if amount:
            text += f" ({_quantity(amount)})"
            evidence += amount.get("evidence") or []
        paragraph.text(f"{separator}{text}", evidence)


def _family(writer: _Writer, number: int, family: dict[str, Any]) -> Paragraph | None:
    paragraph = _Paragraph(writer)
    variant = f" ({family['variant']})" if family.get("variant") else ""
    paragraph.text(
        f"Family {number}, called “{family.get('label')}”{variant}",
        family.get("evidence"),
    )
    paragraph.text(",")
    layer_names = {
        layer.get("layer_id"): str(layer.get("material"))
        for layer in family.get("layers") or []
    }
    steps = _sequence_order(family.get("processing_steps") or [])
    if steps:
        paragraph.text(" is fabricated in the following way:")
        for index, step in enumerate(steps, 1):
            materials = step.get("materials") or []
            targets = [
                layer_names[target]
                for target in step.get("target_layer_ids") or []
                if target in layer_names
            ]
            text = f" ({index}) {step.get('operation')}"
            if materials:
                text += f" of {_join([str(item) for item in materials])}"
            if targets:
                text += f" for the {_join(targets)} layer"
            paragraph.text(text, step.get("evidence"))
            paragraph.values(step.get("conditions") or [], prefix=", with ")
            paragraph.text("." if index == len(steps) else ";")
    else:
        paragraph.text(" has no reported fabrication steps.")

    layers = _sequence_order(family.get("layers") or [])
    details = [
        part
        for part in (
            family.get("polarity") if _reported(family.get("polarity")) else None,
            family.get("architecture"),
        )
        if part
    ]
    if layers:
        paragraph.text(" The fabricated devices have the architecture ")
        for index, layer in enumerate(layers):
            separator = "" if index == 0 else " | "
            role = f" ({_label(layer['role'])})" if _reported(layer.get("role")) else ""
            paragraph.text(
                f"{separator}{layer.get('material')}{role}", layer.get("evidence")
            )
        paragraph.text(f" ({'; '.join(details)})." if details else ".")
    elif details:
        paragraph.text(f" The devices are described as {'; '.join(details)}.")
    else:
        paragraph.text(" No layer stack is reported.")
    if family.get("full_stack_raw"):
        paragraph.text(f" The paper writes the stack as “{family['full_stack_raw']}”.")

    for layer in layers:
        constituents = layer.get("constituents") or []
        properties = layer.get("reported_properties") or []
        if not (constituents or properties or layer.get("material_form_raw")):
            continue
        paragraph.text(f" The {layer.get('material')} layer")
        clauses = 0
        if layer.get("material_form_raw"):
            paragraph.text(f" is described as “{layer['material_form_raw']}”")
            clauses += 1
        if constituents:
            paragraph.text(" contains" if clauses == 0 else "; it contains")
            _constituents(paragraph, constituents)
            clauses += 1
        if properties:
            paragraph.text(" has" if clauses == 0 else "; it has")
            paragraph.values(properties, prefix=" a ")
        paragraph.text(".")

    absorbers = sorted(
        family.get("absorbers") or [],
        key=lambda item: _sort_text(
            (item.get("formula") or {}).get("raw_value"), item.get("label")
        ),
    )
    for absorber in absorbers:
        place = layer_names.get(absorber.get("layer_id"))
        where = f" in the {place} layer" if place else ""
        paragraph.text(
            f" The absorber “{absorber.get('label')}”{where}", absorber.get("evidence")
        )
        formula = absorber.get("formula")
        if formula:
            paragraph.text(
                f" has the formula {_quantity(formula)}", formula.get("evidence")
            )
        if absorber.get("constituents"):
            paragraph.text(", contains" if formula else " contains")
            _constituents(paragraph, absorber["constituents"])
        if absorber.get("properties"):
            paragraph.text(
                "; it has" if formula or absorber.get("constituents") else " has"
            )
            paragraph.values(absorber["properties"], prefix=" a ")
        paragraph.text(".")
    return paragraph.build()


def _subject(paragraph: _Paragraph, subject: dict[str, Any], *, lead: str) -> None:
    stage = subject.get("treatment_stage")
    material = subject.get("treated_material")
    text = lead
    if material:
        text += f" {material}"
    text += " was treated"
    if _reported(stage):
        text += f" {_label(stage)}"
    paragraph.text(text)
    paragraph.values(subject.get("conditions") or [], prefix=" with ", end=".")


def _protocol(
    paragraph: _Paragraph,
    record: dict[str, Any],
    *,
    opening: str = " (",
    closing: str = ")",
) -> None:
    """Append " (reverse J–V scan under spectrum of AM1.5G [n])" when reported."""

    kind = record.get("measurement_type")
    direction = record.get("scan_direction")
    words = []
    if _reported(direction) and direction != "not_applicable":
        words.append(str(direction))
    if _reported(kind):
        words.append(_label(kind))
    conditions = record.get("measurement_conditions") or []
    if not words and not conditions:
        return
    paragraph.text(f"{opening}{' '.join(words) or 'measured'}")
    if conditions:
        paragraph.values(conditions, prefix=" under ")
    paragraph.text(closing)


def _quantities_key(values: list[dict[str, Any]]) -> str:
    return " ".join(f"{value.get('name')}={_quantity(value)}" for value in values)


def render_prose(
    extraction: StudyExtraction | dict[str, Any],
    blocks: list[EvidenceBlock],
    paper: PaperInfo | None = None,
) -> ProseDocument:
    """Render one extraction; citations resolve against that extraction's own blocks.

    ``paper`` is the publication metadata of the comparison. It is deliberately not
    read from the extraction, so both candidates open with the same sentence.
    """

    study = (
        extraction.model_dump(mode="json")
        if isinstance(extraction, StudyExtraction)
        else StudyExtraction.model_validate(extraction).model_dump(mode="json")
    )
    paper = paper or PaperInfo()
    authors = authors_phrase(paper.authors)
    writer = _Writer(blocks)
    sections: dict[SectionKey, list[Paragraph | None]] = {
        key: [] for key, _ in SECTIONS
    }

    families = sorted(study["device_families"], key=_family_key)
    family_number = {
        family["family_id"]: index for index, family in enumerate(families, 1)
    }
    devices = sorted(
        study["individual_devices"],
        key=lambda device: (
            device.get("champion_status") != "yes",
            family_number.get(device.get("family_id"), len(families) + 1),
            _sort_text(device.get("label"), device.get("variant")),
        ),
    )
    device_index = {device["device_id"]: device for device in devices}
    populations = sorted(
        study["population_statistics"],
        key=lambda item: (
            family_number.get(item.get("family_id"), len(families) + 1),
            _sort_text(item.get("label"), _quantities_key(item.get("metrics") or [])),
        ),
    )

    # Study: who did what, in the same words for both candidates.
    intro = _Paragraph(writer)
    published = ", ".join(
        str(part) for part in (paper.journal, paper.year) if part is not None
    )
    title = f" in “{paper.title}”" if paper.title else ""
    title += f" ({published})" if published else ""
    intro.text(
        f"{authors}{title} describe the fabrication of "
        f"{_count(len(families), 'device family', 'device families')}."
    )
    tests = study["stability_tests"]
    intro.text(
        f" They characterize {_count(len(devices), 'individual device')} and "
        f"{_count(len(populations), 'device population')}, and report "
        f"{_count(len(tests), 'stability test')}."
    )
    sections["study"].append(intro.build())

    for number, family in enumerate(families, 1):
        sections["devices"].append(_family(writer, number, family))

    def family_of(record: dict[str, Any]) -> str:
        number = family_number.get(record.get("family_id"))
        return f"family {number}" if number else ""

    def device_name(device: dict[str, Any]) -> str:
        kind = "champion device" if device.get("champion_status") == "yes" else "device"
        variant = f" ({device['variant']})" if device.get("variant") else ""
        family = f" of {family_of(device)}" if family_of(device) else ""
        return f"the {kind} “{device.get('label')}”{variant}{family}"

    # Results: populations compared across families, then individual devices.
    groups: dict[tuple[str, ...], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for population in populations:
        for metric in population.get("metrics") or []:
            group_key = _sort_text(population.get("statistic_type"), metric.get("name"))
            groups.setdefault(group_key, []).append((population, metric))
    paragraph = _Paragraph(writer)
    for group_index, members in enumerate(groups.values()):
        first_population, first_metric = members[0]
        statistic = STATISTICS.get(
            str(first_population.get("statistic_type")), "an aggregate"
        )
        subject = authors if group_index == 0 else " They"
        paragraph.text(f"{subject} report {statistic} {first_metric.get('name')} of ")
        for index, (population, metric) in enumerate(members):
            if index == 1:
                paragraph.text(", as compared to ")
            elif index > 1:
                paragraph.text(" and " if index == len(members) - 1 else ", ")
            start = paragraph.mark()
            paragraph.text(_quantity(metric), metric.get("evidence"))
            where = family_of(population) or "the population"
            details = [f"“{population.get('label')}”"]
            if population.get("sample_size"):
                details.append(f"n = {population['sample_size']}")
            if _reported(population.get("error_bar_type")):
                details.append(f"error bars as {_label(population['error_bar_type'])}")
            paragraph.text(f" for {where} ({', '.join(details)}")
            _protocol(paragraph, population, opening=", ", closing="")
            paragraph.text(")", population.get("evidence"), since=start)
        paragraph.text(".")
    for population in populations:
        for subject_context in population.get("subjects") or []:
            _subject(
                paragraph,
                subject_context,
                lead=f" For the population “{population.get('label')}”,",
            )
    sections["results"].append(paragraph.build())

    observations: dict[object, list[dict[str, Any]]] = {}
    for observation in study["performance_observations"]:
        owner = observation.get("device_id")
        observations.setdefault(owner if owner in device_index else None, []).append(
            observation
        )
    for group in observations.values():
        group.sort(
            key=lambda item: _sort_text(
                item.get("measurement_type"),
                item.get("scan_direction"),
                _quantities_key(item.get("metrics") or []),
            )
        )
    champions = [d for d in devices if d.get("champion_status") == "yes"]
    for heading, selected in (
        ("What concerns champion devices,", champions),
        ("Further,", [d for d in devices if d.get("champion_status") != "yes"]),
    ):
        paragraph = _Paragraph(writer)
        for index, device in enumerate(selected):
            measured = observations.get(device["device_id"], [])
            if not measured:
                paragraph.text(f" For {device_name(device)}", device.get("evidence"))
                paragraph.text(", no measurements are reported.")
            else:
                subject = "the authors" if index == 0 else "they"
                paragraph.text(
                    f" {heading if index == 0 else 'Also,'} {subject} measured "
                )
                start = paragraph.mark()
                for position, observation in enumerate(measured):
                    if position:
                        paragraph.text(
                            " and " if position == len(measured) - 1 else "; "
                        )
                    record = paragraph.mark()
                    paragraph.values(observation.get("metrics") or [])
                    _protocol(paragraph, observation)
                    paragraph.text("", observation.get("evidence"), since=record)
                paragraph.text(f" for {device_name(device)}")
                paragraph.text("", device.get("evidence"), since=start)
                paragraph.text(".")
            if device.get("reported_properties"):
                paragraph.values(
                    device["reported_properties"], prefix=" This device has ", end="."
                )
            if device.get("subject_context"):
                _subject(paragraph, device["subject_context"], lead=" Here,")
        sections["results"].append(paragraph.build())
    paragraph = _Paragraph(writer)
    for observation in observations.get(None, []):
        paragraph.text(" In addition, the authors measured ")
        paragraph.values(observation.get("metrics") or [])
        _protocol(paragraph, observation)
        paragraph.text(" without naming the device.", observation.get("evidence"))
    sections["results"].append(paragraph.build())

    # Stability: what each aging test yielded.
    device_order = {device["device_id"]: index for index, device in enumerate(devices)}

    def specimen(test: dict[str, Any]) -> str:
        if test.get("device_id") in device_index:
            return device_name(device_index[test["device_id"]])
        if test.get("family_id") in family_number:
            return f"devices of family {family_number[test['family_id']]}"
        return f"the specimen “{test.get('specimen_label')}”"

    tests = sorted(
        tests,
        key=lambda test: (
            device_order.get(test.get("device_id"), len(devices)),
            family_number.get(test.get("family_id"), len(families) + 1),
            _sort_text(
                test.get("specimen_label"),
                _quantities_key(test.get("conditions") or []),
            ),
        ),
    )
    for index, test in enumerate(tests):
        paragraph = _Paragraph(writer)
        lead = "Stability investigations yielded the following. " if index == 0 else ""
        name = specimen(test)
        paragraph.text(f"{lead}{name[:1].upper()}{name[1:]}", test.get("evidence"))
        if test.get("conditions"):
            paragraph.values(test["conditions"], prefix=" was aged with ", end=".")
        else:
            paragraph.text(" was aged under unreported conditions.")
        checkpoints = sorted(
            test.get("checkpoints") or [],
            key=lambda point: (
                (point.get("time") or {}).get("value_number") is None,
                (point.get("time") or {}).get("value_number") or 0,
                _sort_text(_quantity(point.get("time") or {})),
            ),
        )
        for point in checkpoints:
            time = point.get("time")
            evidence = [*((time or {}).get("evidence") or []), *point["evidence"]]
            when = f"After {_quantity(time)}" if time else "At an unreported time"
            paragraph.text(f" {when}", evidence)
            if point.get("conditions"):
                paragraph.values(point["conditions"], prefix=" with ")
            paragraph.values(
                point.get("outcomes") or [], prefix=", the authors observed ", end="."
            )
        sections["stability"].append(paragraph.build())

    result = []
    for key, title in SECTIONS:
        paragraphs = [item for item in sections[key] if item is not None]
        if not paragraphs and EMPTY[key]:
            paragraphs = [Paragraph(segments=[Segment(text=EMPTY[key])])]
        result.append(Section(key=key, title=title, paragraphs=paragraphs))
    return ProseDocument(sections=result, references=writer.references)
