"""Rebuild numeric tables printed inside figures from the words inside them.

Docling recognises figures but does not hand their inner text to the evidence stream,
so a table drawn inside a figure (for example the J-V parameters inset in a J-V plot)
never reaches the model. Docling's figure text, where available, also comes as large
fragments that mix rows. This module combines the backends: Docling supplies the
figure regions, PyMuPDF (or, for image figures, RapidOCR) the words inside them with
exact positions.

Inside each figure region, axis tick labels are recognised as numbers at evenly spaced
positions whose values are evenly spaced too (linear or logarithmic axes); a column of
them is removed from a table only where it continues above or below the table, since a
table column may legitimately hold evenly spaced values, and lines that only continue
axis scales are no rows. The remaining words are grouped into printed lines. Adjacent
lines that share at least two numeric columns form a table (at least two rows and six
numbers, rows mostly numeric and holding different values); content of neighbouring
panels, such as a legend beside the table, is separated where the empty space between
columns jumps. Its row labels, units and the header lines directly above (which name
quantities, so a line of numbers is none) are kept. Each table becomes one block of
kind ``figure_table`` whose text is exactly the words read, one printed row per line,
so every row is citable verbatim. A device's parameters printed one per line
("PCE: 20.1%" / "Voc: 1.15 V" / ...) have one number per line and form no columns; such
stacks of at least three left-aligned "label: value [unit]" lines with distinct labels
are read separately (``_label_values``). All distances are measured in text heights,
so no rule depends on the size of a particular figure.

Figures stored as images have no text layer. With ``ocr`` enabled, a figure region that
is mostly covered by raster images and yields no table from its text layer is rendered
and read by RapidOCR (the OCR engine Docling already uses); its word boxes go through
the same rules. OCR can misread characters, so such blocks say so in their section
label and record the lowest word confidence; the PDF text layer is always preferred.

Colour is recorded as metadata, not as text: figures often mark series (reference vs
treated) only by colour. Each row's dominant number colour and the coloured legend
markers inside the figure are kept for reviewers; assigning a series from colour is an
inference and is never written into the quotable text. For OCR'd figures the row colour
is the ink colour of the pixels, rounded to a coarse palette, and legends are not read.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

from loguru import logger

NUMERIC_WORD = re.compile(
    r"^[(\[]?[-+−–]?\d+(?:[.,]\d+)?(?:[eE][-+−–]?\d+)?(?:±\d+(?:[.,]\d+)?)?%?[)\]]?[,;]?$"
)
SECTION_LABEL = "Numbers printed inside a figure (rows rebuilt from the PDF text layer)"
OCR_SECTION_LABEL = "Numbers read by OCR from a figure image (rows rebuilt from OCR words; digits may be misread)"
# OCR: default render resolution, its allowed range, and the share of the figure
# region raster images must cover.
OCR_DPI = 300
OCR_DPI_RANGE = (72, 1200)
MIN_RASTER_SHARE = 0.5
FIGURE_NUMBER = re.compile(
    r"^\s*((?:Supplementary\s+|Extended\s+Data\s+)?(?:Fig(?:ure)?s?\.?)\s*S?\d+[A-Za-z]?)",
    re.IGNORECASE,
)
# A figure table needs at least this many aligned numeric columns, rows and numbers.
MIN_COLUMNS, MIN_ROWS, MIN_CELLS = 2, 2, 6
# ... and in a typical row at least this share of the words must be numbers.
MIN_NUMBER_SHARE = 0.5
# A stack of "label: value" lines needs at least this many lines with distinct labels.
MIN_LABEL_VALUES = 3
SEPARATOR = re.compile(r"([:=])")
LABEL_VALUE = re.compile(
    r"^[(\[]?[-+−–]?\d+(?:[.,]\d+)?(?:[eE][-+−–]?\d+)?(?:±\d+(?:[.,]\d+)?)?"
    r"(?P<unit>[%°A-Za-zµμΩ/·⋅^{}⁻¹²³\-−]*\d?)[)\]]?[,;]?$"
)
EXPONENT = re.compile(r"^[-−⁻]?\d$")
UNIT_WORD = re.compile(r"^(?=.*[A-Za-z%°µμΩ])[%°A-Za-zµμΩ/·⋅^{}()⁻¹²³\-−\d]{1,12}[,;]?$")


def section_label(caption: str | None, *, ocr: bool = False) -> str:
    """Name the figure in the label, so the block stays linked to it anywhere."""
    label = OCR_SECTION_LABEL if ocr else SECTION_LABEL
    match = FIGURE_NUMBER.match(caption or "")
    if not match:
        return label
    number = re.sub(r"\s+", " ", match.group(1)).strip()
    return f"{number}: {label[0].lower()}{label[1:].replace(' a figure', ' the figure')}"


def _is_number(text: str) -> bool:
    return NUMERIC_WORD.match(text) is not None


def _value(text: str) -> tuple[float, float] | None:
    """Printed value and half a unit of its last printed digit (its rounding)."""
    cleaned = re.sub(r"[()\[\]%,;]", "", text.split("±")[0]).replace("−", "-").replace("–", "-")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    mantissa = cleaned.lower().split("e")[0]
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    return value, 0.5 * 10.0 ** (-decimals)


def _magnitudes(row: list[tuple]) -> set[float]:
    """The distinct absolute values printed in a row."""
    return {abs(v[0]) for v in (_value(w[4]) for w in row if _is_number(w[4])) if v}


def _centre(word: tuple) -> tuple[float, float]:
    return (word[0] + word[2]) / 2, (word[1] + word[3]) / 2


def _even(values: list[float], unit: float) -> bool:
    """Evenly spaced as printed: equal steps, allowing one last-digit unit of rounding
    only for steps of at least five units (0.33, 0.67, 1.00), never for small steps."""
    steps = [b - a for a, b in zip(values, values[1:])]
    if not steps or steps[0] == 0:
        return False
    spread = max(steps) - min(steps)
    if spread <= 1e-9 * max(1.0, abs(steps[0])):
        return True
    return min(abs(s) for s in steps) >= 5 * unit and spread <= unit * 1.0001


def _axis_runs(words: list[tuple], height: float) -> tuple[set[int], list[set[int]]]:
    """Axis tick labels: numbers at evenly spaced positions with evenly spaced values.

    Returns the indices of horizontal tick runs (a line of three or more such numbers,
    removed outright: a table row practically never holds evenly spaced values of
    different quantities) and the vertical runs (columns of such numbers). A vertical
    run can also be a table column with evenly spaced values (0, 0.5, 1.0 mol%); it is
    only treated as an axis where it continues beyond the table, see ``_rows``.
    Linear axes (equal steps) and logarithmic axes (equal factors of at least 1.5) are
    recognised; tolerances are in text heights or in units of the last printed digit.
    """
    numeric = [(i, w, _value(w[4])) for i, w in enumerate(words) if _is_number(w[4])]
    numeric = [(i, w, v) for i, w, v in numeric if v is not None]
    position_tolerance = 0.35 * height

    def runs_in(group: list[tuple], axis: int) -> list[set[int]]:
        group = sorted(group, key=lambda item: _centre(item[1])[axis])
        found: list[set[int]] = []
        start = 0
        while start < len(group) - 2:
            for stop in range(len(group), start + 2, -1):
                run = group[start:stop]
                positions = [_centre(w)[axis] for _, w, _ in run]
                spacing = [b - a for a, b in zip(positions, positions[1:])]
                if min(spacing) <= 0.5 * height:
                    continue
                if max(spacing) - min(spacing) > max(position_tolerance, 0.15 * max(spacing)):
                    continue
                values = [v[0] for _, _, v in run]
                unit = 2 * max(v[1] for _, _, v in run)
                # log axes step by a constant, clear factor (x2, x5, x10)
                ratios = [b / a for a, b in zip(values, values[1:])] if all(v > 0 for v in values) else []
                logarithmic = bool(ratios) and min(ratios) >= 1.5 and max(ratios) <= 1.02 * min(ratios)
                if _even(values, unit) or logarithmic:
                    found.append({i for i, _, _ in run})
                    start = stop - 1
                    break
            else:
                start += 1
        return found

    def clusters(key) -> list[list[tuple]]:
        groups: list[list[tuple]] = []
        for item in sorted(numeric, key=key):
            if groups and key(item) - key(groups[-1][-1]) <= position_tolerance:
                groups[-1].append(item)
            else:
                groups.append([item])
        return [g for g in groups if len(g) >= 3]

    horizontal: set[int] = set()
    for group in clusters(lambda item: _centre(item[1])[1]):  # numbers on one line
        for run in runs_in(group, 0):
            horizontal |= run
    vertical: list[set[int]] = []
    for key in (lambda item: item[1][2], lambda item: _centre(item[1])[0]):  # right-aligned, centred
        for group in clusters(key):
            vertical.extend(runs_in(group, 1))
    return horizontal, vertical


def _lines(words: list[tuple]) -> list[list[tuple]]:
    """Printed lines: a word joins a line when it overlaps the line's first word."""
    lines: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        for line in lines:
            seed = line[0]
            overlap = min(word[3], seed[3]) - max(word[1], seed[1])
            if overlap >= 0.5 * min(word[3] - word[1], seed[3] - seed[1]):
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w[0]) for line in sorted(lines, key=lambda first: first[0][1])]


def _chunks(line: list[tuple], height: float) -> list[list[tuple]]:
    """Pieces of a printed line separated by more than two text heights."""
    chunks, current = [], [line[0]]
    for word in line[1:]:
        if word[0] - current[-1][2] > 2 * height:
            chunks.append(current)
            current = [word]
        else:
            current.append(word)
    chunks.append(current)
    return chunks


def _shared_columns(lines: list[list[tuple]], tolerance: float) -> list[tuple[float, list[tuple]]]:
    """Numeric columns that at least two of the lines share: (centre x, its words)."""
    anchors: list[list] = []  # [centre, right, set of line indices, words]
    for index, line in enumerate(lines):
        for word in line:
            if not _is_number(word[4]):
                continue
            centre, right = (word[0] + word[2]) / 2, word[2]
            for anchor in anchors:
                if abs(centre - anchor[0]) <= tolerance or abs(right - anchor[1]) <= tolerance:
                    anchor[2].add(index)
                    anchor[3].append(word)
                    break
            else:
                anchors.append([centre, right, {index}, [word]])
    return sorted(((a[0], a[3]) for a in anchors if len(a[2]) >= 2), key=lambda c: c[0])


def _column_groups(columns: list[tuple[float, list[tuple]]]) -> list[list[float]]:
    """Split columns where the empty space between them jumps.

    ``columns`` holds (centre, words) from ``_shared_columns``. The empty space between
    two neighbouring columns runs from the right edge of the left one's words to the
    left edge of the right one's. Where it is more than three times the typical space
    between columns, it separates one panel's table from content of another panel (a
    legend, an axis). Empty space is used rather than the distance between centres, so
    wide label cells or long numbers do not hide or fake such a jump. Returns the
    centres of each group.
    """
    if not columns:
        return []
    gaps = [
        min(w[0] for w in right) - max(w[2] for w in left)
        for (_, left), (_, right) in zip(columns, columns[1:])
    ]
    typical = statistics.median(gaps) if gaps else 0.0
    groups = [[columns[0][0]]]
    for (centre, _), gap in zip(columns[1:], gaps):
        if typical > 0 and gap > 3 * typical:
            groups.append([centre])
        else:
            groups[-1].append(centre)
    return groups


def _rows(words: list[tuple]) -> list[dict[str, Any]]:
    """Tables inside one figure region.

    Adjacent printed lines that share at least two numeric columns form a table. Its
    columns are those whose spacing stays regular (``_column_groups``). Each row keeps
    the pieces of text that reach into the table's columns, which includes units after
    the last value, plus its row label: the piece just left of the first column, kept
    when it is close (within one column spacing) or when it starts where the labels of
    other rows start, so left-aligned labels of any length stay. Vertical tick runs that
    continue beyond the table's rows are axes and are removed.
    """
    if not words:
        return []
    height = statistics.median(w[3] - w[1] for w in words)
    tolerance = 0.8 * height  # how far apart two numbers of one column may sit
    horizontal, vertical = _axis_runs(words, height)
    index_of = {id(w): i for i, w in enumerate(words)}
    kept = [w for i, w in enumerate(words) if i not in horizontal]
    if not kept:
        return []
    lines = _lines(kept)

    # a line joins a table when it is close below the table's last row and shares at
    # least two numeric columns with it; lines in between that share nothing (an axis
    # tick between two rows) neither join nor interrupt it. Where several open tables fit
    # a line (side-by-side panels), it joins the one it shares most columns with.
    runs: list[list[int]] = []
    for index, line in enumerate(lines):
        top = min(w[1] for w in line)
        candidates = []
        for run in runs:
            last = lines[run[-1]]
            gap = top - max(w[3] for w in last)
            shared = len(_shared_columns([last, line], tolerance))
            # boxes of separate lines may overlap (raised or lowered words), so only the
            # distance down to the next line is limited
            if gap <= 2 * height and shared >= MIN_COLUMNS:
                candidates.append((shared, run))
        if candidates:
            max(candidates, key=lambda c: c[0])[1].append(index)
        else:
            runs.append([index])

    tables = []
    for run in (r for r in runs if len(r) >= MIN_ROWS):
        table_columns = _shared_columns([lines[i] for i in run], tolerance)
        groups = _column_groups(table_columns)
        columns = max(groups, key=len) if groups else []
        if len(columns) < MIN_COLUMNS:
            continue
        # a table needs at least two columns that are not themselves axis scales:
        # identical y axes of neighbouring panels share "columns" but are no table
        tick_ids = {i for column in vertical for i in column}
        real = [
            centre for centre, column_words in table_columns
            if centre in columns and sum(index_of[id(w)] in tick_ids for w in column_words) < 3
        ]
        if len(real) < MIN_COLUMNS:
            continue
        # every data row has a value in one of those columns; lines that only continue
        # axis scales (the ticks of two panels side by side) are no rows of the table,
        # and the table spans its real columns only
        real_words = {id(w) for centre, column_words in table_columns if centre in real for w in column_words}
        run = [i for i in run if any(id(w) in real_words for w in lines[i])]
        if len(run) < MIN_ROWS:
            continue
        columns = real
        spacing = statistics.median([b - a for a, b in zip(columns, columns[1:])])
        low, high = columns[0] - tolerance, columns[-1] + tolerance

        rows: list[list[tuple]] = []
        labels: list[list[tuple] | None] = []
        for i in run:
            chunks = _chunks(lines[i], height)
            core = [c for c in chunks if any(low <= (w[0] + w[2]) / 2 <= high for w in c)]
            if not core:
                rows.append([])
                labels.append(None)
                continue
            before = [c for c in chunks if c[-1][2] <= core[0][0][0]]
            rows.append([w for c in core for w in c])
            labels.append(before[-1] if before else None)

        # pass 1: labels close to the first column; their starts define the label column
        starts = [row[0][0] for row in rows if row and not _is_number(row[0][4])]
        for k, label in enumerate(labels):
            if label and rows[k] and rows[k][0][0] - label[-1][2] <= spacing:
                rows[k] = label + rows[k]
                starts.append(label[0][0])
                labels[k] = None
        # pass 2: farther labels that start where the other rows' labels start
        for k, label in enumerate(labels):
            if label and rows[k] and any(abs(label[0][0] - x) <= tolerance for x in starts):
                rows[k] = label + rows[k]

        present = [row for row in rows if row]
        if not present:
            continue
        x0 = min(w[0] for row in present for w in row)
        x1 = max(w[2] for row in present for w in row)
        top = min(w[1] for w in lines[run[0]])
        headers: list[list[tuple]] = []
        for index in range(run[0] - 1, -1, -1):
            within = [w for w in lines[index] if w[2] >= x0 - height and w[0] <= x1 + height]
            if not within:
                continue  # text of another panel at this height: look further up
            if all(_is_number(w[4]) for w in within):
                continue  # headers name quantities and units; a line of numbers is ticks
            if top - max(w[3] for w in within) > 2 * height or len(headers) == 3:
                break
            headers.insert(0, within)
            top = min(w[1] for w in within)

        members = {index_of[id(w)] for row in present for w in row}
        row_top = min(w[1] for w in lines[run[0]])
        row_bottom = max(w[3] for w in lines[run[-1]])
        axis: set[int] = set()
        for column in vertical:
            if column & members and any(
                words[i][3] < row_top - 0.2 * height or words[i][1] > row_bottom + 0.2 * height for i in column
            ):
                axis |= column

        def clean(row: list[tuple]) -> list[tuple]:
            return [w for w in row if index_of[id(w)] not in axis]

        data_rows = [r for r in (clean(row) for row in present) if r]
        header_rows = [r for r in (clean(row) for row in headers) if r]
        cells = sum(_is_number(w[4]) for row in data_rows for w in row)
        # table rows consist mostly of numbers; prose and reference lists, whose years
        # and volumes can line up by chance, do not
        if len(data_rows) < MIN_ROWS:
            continue
        density = statistics.median(sum(_is_number(w[4]) for w in row) / len(row) for row in data_rows)
        # a table row holds different values; a row that repeats one value ("2 days
        # 2 days", "5 5") is a legend or an axis repeated across panels. The sign is
        # ignored, since OCR easily adds or drops a minus sign.
        repeated = sum(len(_magnitudes(row)) < 2 for row in data_rows)
        if (
            2 * repeated >= len(data_rows)
            or len(data_rows) < MIN_ROWS
            or cells < MIN_CELLS
            or density < MIN_NUMBER_SHARE
            or len(_shared_columns(data_rows, tolerance)) < MIN_COLUMNS
        ):
            continue
        tables.append({"rows": [*header_rows, *data_rows], "data_rows": len(data_rows)})
    return tables


def _pairs(chunk: list[tuple]) -> list[dict[str, Any]] | None:
    """The "label: value [unit]" pairs a piece of a line consists of, or None.

    A pair is one to four label words (the first starting with a letter), a colon or an
    equals sign, one number and at most two unit words; the piece must consist of pairs
    only, so prose ("a PCE of 20.1% (VOC: 1.22 V, JSC: ...") and expressions
    ("y = 0.5x + 2") are none. Separators may be printed apart or attached to a word.
    """
    tokens: list[tuple[str, tuple]] = []
    for word in chunk:
        tokens += [(part, word) for part in SEPARATOR.split(word[4]) if part.strip()]
    pairs: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        start = i
        while i < len(tokens) and tokens[i][0] not in ":=" and i - start < 4:
            i += 1
        if i == start or i >= len(tokens) or tokens[i][0] not in ":=" or not tokens[start][0][0].isalpha():
            return None
        label = " ".join(t for t, _ in tokens[start:i]).lower()
        i += 1
        if i >= len(tokens):
            return None
        value = LABEL_VALUE.match(tokens[i][0])
        if not value:
            return None
        number = tokens[i][0]
        i += 1
        units = 0
        while (
            i < len(tokens)
            and units < 2
            # a unit word, or the raised exponent of one read apart ("mA/cm" "2")
            and (UNIT_WORD.match(tokens[i][0]) or (units and EXPONENT.match(tokens[i][0])))
            and not (i + 1 < len(tokens) and tokens[i + 1][0] in ":=")
        ):
            i += 1
            units += 1
        words: list[Any] = []
        for _, word in tokens[start:i]:
            if not words or words[-1] is not word:
                words.append(word)
        pairs.append({
            "label": label,
            "number": number,
            "words": words,
        })
    return pairs or None


def _label_values(words: list[tuple]) -> list[dict[str, Any]]:
    """Stacks of "label: value" lines (PCE: 20.1% / Voc: 1.15 V / Jsc: ... / FF: ...).

    A device's parameters are often printed one per line inside a J-V plot; such a stack
    has one number per line and never forms a table of shared numeric columns. A stack
    is at least ``MIN_LABEL_VALUES`` adjacent lines whose pairs start at the same left
    edge, with distinct labels and different values; stacks side by side (two devices,
    labels repeated) are read separately. One header line directly above it that names
    no value ("Champion device", "Control") is kept.
    """
    if not words:
        return []
    height = statistics.median(w[3] - w[1] for w in words)
    lines = _lines(words)
    found = []  # (line index, pair)
    for index, line in enumerate(lines):
        for chunk in _chunks(line, height):
            found += [(index, pair) for pair in _pairs(chunk) or []]
    stacks: list[list[tuple[int, dict]]] = []
    for index, pair in found:
        x0 = pair["words"][0][0]
        for stack in stacks:
            last_index, last = stack[-1]
            gap = min(w[1] for w in pair["words"]) - max(w[3] for w in last["words"])
            if last_index < index and gap <= 2 * height and abs(last["words"][0][0] - x0) <= height:
                stack.append((index, pair))
                break
        else:
            stacks.append([(index, pair)])

    tables = []
    for stack in stacks:
        labels = [pair["label"] for _, pair in stack]
        values = {abs(v[0]) for v in (_value(re.sub(r"[^\d.,eE±+\-−–]", "", p["number"])) for _, p in stack) if v}
        if len(stack) < MIN_LABEL_VALUES or len(set(labels)) < len(labels) or len(values) < 2:
            continue
        rows = [pair["words"] for _, pair in stack]
        x0 = min(w[0] for row in rows for w in row)
        x1 = max(w[2] for row in rows for w in row)
        top = min(w[1] for w in rows[0])
        headers = []
        for index in range(stack[0][0] - 1, -1, -1):
            within = [w for w in lines[index] if w[2] >= x0 - height and w[0] <= x1 + height]
            if not within:
                continue
            text = "".join(w[4] for w in within)
            if (
                top - max(w[3] for w in within) <= 2 * height
                and sum(c.isalpha() for c in text) >= 2  # a name, not a stray unit letter
                and not any(_is_number(w[4]) or SEPARATOR.search(w[4]) for w in within)
            ):
                headers = [within]
            break
        tables.append({"rows": [*headers, *rows], "data_rows": len(rows)})
    return tables


def _horizontal_words(page, rect) -> list[tuple]:
    """Words of the page, without rotated text such as vertical axis titles.

    PyMuPDF records each text line's writing direction; words whose centre lies in a
    line that is not horizontal are dropped, since they cannot be part of a table row.
    """
    rotated = []
    for block in page.get_text("dict", clip=rect)["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            if abs(dy) > 0.1 or dx < 0:
                rotated.append(line["bbox"])
    words = []
    for word in page.get_text("words"):
        if not word[4].strip():
            continue
        cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in rotated):
            continue
        words.append(word)
    return words


def _hex(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return f"#{value:06x}"
    if len(value) == 1:
        value = (value[0],) * 3
    if len(value) == 4:
        c, m, y, k = value
        value = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in value[:3])


def _span_colours(page, rect) -> list[tuple]:
    spans = []
    for block in page.get_text("dict", clip=rect)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append((span["bbox"], _hex(span["color"])))
    return spans


def _colour_of(word: tuple, spans: list[tuple]) -> str | None:
    cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
    for (x0, y0, x1, y1), colour in spans:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return colour
    return None


def _legend(page, rect, height: float) -> list[dict[str, Any]]:
    """Short labels with a coloured line or marker just to their left.

    Distances are measured in text heights, so the rule does not depend on the size of
    the figure: a marker is at most eight text heights wide and two high, at most four
    text heights left of its label, and legend entries are stacked below each other.
    """
    import pymupdf

    marks = []
    for drawing in page.get_drawings():
        box = drawing["rect"]
        if not rect.intersects(box) or box.width > 8 * height or box.height > 2 * height:
            continue
        colours = {
            colour
            for colour in (_hex(drawing.get("color")), _hex(drawing.get("fill")))
            if colour is not None and colour not in {"#000000", "#ffffff"}
        }
        if colours:
            marks.append((box, colours))
    entries = []
    for block in page.get_text("dict", clip=rect)["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"].strip() for s in line["spans"]).strip()
            if not re.search(r"[A-Za-z]", text) or len(text) > 30:
                continue
            box = pymupdf.Rect(line["bbox"])
            near: set[str] = set()
            for mark, colours in marks:
                beside = 0 <= box.x0 - mark.x1 <= 4 * height
                level = mark.y0 - 0.3 * height <= (box.y0 + box.y1) / 2 <= mark.y1 + 0.3 * height
                if beside and level:
                    near |= colours
            if near:
                entries.append({"label": text, "marker_colours": sorted(near), "x": box.x0, "y": box.y0})
    stacked = [
        e
        for e in entries
        if any(
            o is not e and abs(o["x"] - e["x"]) <= 0.5 * height and abs(o["y"] - e["y"]) <= 2.5 * height
            for o in entries
        )
    ]
    return [{"label": e["label"], "marker_colours": e["marker_colours"]} for e in stacked]


def _raster_share(page, rect) -> float:
    """Share of the region covered by raster images (overlaps may count twice)."""
    import pymupdf

    covered = 0.0
    for info in page.get_image_info():
        box = pymupdf.Rect(info["bbox"]) & rect
        if not box.is_empty:
            covered += box.width * box.height
    return min(1.0, covered / max(rect.width * rect.height, 1e-9))


def _ocr_words(engine, page, rect, dpi: int = OCR_DPI) -> tuple[list[tuple], Any, tuple[float, float]]:
    """OCR words of a rendered region as PyMuPDF-style word tuples in page coordinates.

    Each tuple is ``(x0, y0, x1, y1, text, confidence)``. Rotated text is dropped like
    in the text-layer route: RapidOCR returns such a line as a box taller than wide.
    Also returns the rendered image and its pixels per point, for ink colours.
    """
    import numpy as np
    import pymupdf

    pixmap = page.get_pixmap(clip=rect, dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    image: Any = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, 3)
    scale = (pixmap.w / rect.width, pixmap.h / rect.height)
    result = engine(image, return_word_box=True)
    words = []
    for line_box, text, line_words in zip(
        result.boxes if result.boxes is not None else [],
        result.txts or [],
        result.word_results or [],
    ):
        xs, ys = [p[0] for p in line_box], [p[1] for p in line_box]
        if len(text.strip()) >= 2 and max(ys) - min(ys) > max(xs) - min(xs):
            continue
        for word, confidence, quad in line_words:
            if not word.strip():
                continue
            wx, wy = [p[0] for p in quad], [p[1] for p in quad]
            words.append(
                (
                    rect.x0 + min(wx) / scale[0],
                    rect.y0 + min(wy) / scale[1],
                    rect.x0 + max(wx) / scale[0],
                    rect.y0 + max(wy) / scale[1],
                    word.strip(),
                    float(confidence),
                )
            )
    return words, image, scale


def _ink_colour(word: tuple, image, rect, scale: tuple[float, float]) -> str | None:
    """Colour of the darker half of a word's pixels, rounded to steps of 0x33."""
    x0 = max(0, int((word[0] - rect.x0) * scale[0]))
    x1 = int((word[2] - rect.x0) * scale[0]) + 1
    y0 = max(0, int((word[1] - rect.y0) * scale[1]))
    y1 = int((word[3] - rect.y0) * scale[1]) + 1
    pixels = image[y0:y1, x0:x1].reshape(-1, 3).astype(float)
    if not len(pixels):
        return None
    brightness = pixels.mean(axis=1)
    if brightness.max() - brightness.min() < 40:
        return None
    import numpy as np

    ink = pixels[brightness <= (brightness.max() + brightness.min()) / 2]
    return "#" + "".join(f"{min(255, round(v / 51) * 51):02x}" for v in np.median(ink, axis=0))


def _ocr_engine():
    """RapidOCR as installed with Docling, or None (logged once) when unavailable."""
    try:
        from rapidocr import RapidOCR

        return RapidOCR(params={"Global.log_level": "error"})
    except Exception as exc:  # noqa: BLE001 - OCR is an optional extra of Docling
        logger.warning("Figure table OCR unavailable ({}); image figures are skipped", exc)
        return None


def figure_tables(
    path: Path, figures: list[dict[str, Any]], *, ocr: bool = False, ocr_dpi: int = OCR_DPI
) -> list[dict[str, Any]]:
    """Tables inside the given figure regions.

    ``figures`` holds ``{"page": 1-based page, "bbox": [left, top, right, bottom],
    "bottom_left_origin": bool}``; Docling usually measures from the bottom of the page,
    PyMuPDF from the top. Returns one item per table with its text, the figure region,
    and colour metadata; callers turn the items into evidence blocks. With ``ocr``, a
    region mostly covered by raster images whose text layer gives no table is read by
    RapidOCR instead (see the module notes), rendered at ``ocr_dpi``.
    """
    import pymupdf

    found: list[dict[str, Any]] = []
    engine: Any = None
    ocr_figures = 0
    with pymupdf.open(path) as document:
        for figure_index, figure in enumerate(figures):
            if not 1 <= figure["page"] <= document.page_count:
                continue
            page = document[figure["page"] - 1]
            left, top, right, bottom = figure["bbox"]
            if figure.get("bottom_left_origin"):
                top, bottom = page.rect.height - top, page.rect.height - bottom
            rect = pymupdf.Rect(left, min(top, bottom), right, max(top, bottom)) & page.rect
            if rect.is_empty:
                continue
            words = [
                word
                for word in _horizontal_words(page, rect)
                if rect.contains(pymupdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))
            ]
            tables = _detect(words, figure["page"])
            image = scale = None
            if not tables and ocr and _raster_share(page, rect) >= MIN_RASTER_SHARE:
                if engine is None:
                    engine = _ocr_engine() or False
                if engine:
                    ocr_figures += 1
                    try:
                        words, image, scale = _ocr_words(engine, page, rect, ocr_dpi)
                    except Exception as exc:  # noqa: BLE001 - one odd image must not fail the parse
                        logger.debug("Figure OCR failed on page {}: {}", figure["page"], exc)
                        words = []
                    tables = _detect(words, figure["page"])
            if not tables:
                continue
            read_by_ocr = image is not None
            if read_by_ocr:
                legend: list[dict[str, Any]] = []
            else:
                spans = _span_colours(page, rect)
                legend = _legend(page, rect, statistics.median(w[3] - w[1] for w in words))
            for table in tables:
                rows = table["rows"]
                text = "\n".join(" ".join(w[4] for w in row) for row in rows)
                row_colours = []
                for row in rows:
                    weights: dict[str, int] = {}
                    for word in row:
                        if _is_number(word[4]):
                            colour = (
                                _ink_colour(word, image, rect, scale)
                                if read_by_ocr and scale is not None
                                else _colour_of(word, spans)
                            )
                            if colour:
                                weights[colour] = weights.get(colour, 0) + len(word[4])
                    row_colours.append(max(weights, key=lambda colour: weights[colour]) if weights else None)
                all_words = [w for row in rows for w in row]
                metadata: dict[str, Any] = {
                    "derived_from": "docling_figure_region+"
                    + ("rapidocr_words" if read_by_ocr else "pymupdf_words"),
                    "figure_bbox": [round(v, 2) for v in rect],
                    "data_rows": table["data_rows"],
                    "row_text_colours": row_colours,
                    "legend_marker_colours": legend,
                }
                if read_by_ocr:
                    metadata["ocr_dpi"] = ocr_dpi
                    metadata["ocr_min_word_confidence"] = round(min(w[5] for w in all_words), 3)
                found.append(
                    {
                        "figure_index": figure_index,
                        "page": figure["page"],
                        "bbox": [
                            min(w[0] for w in all_words),
                            min(w[1] for w in all_words),
                            max(w[2] for w in all_words),
                            max(w[3] for w in all_words),
                        ],
                        "text": text,
                        "read_by_ocr": read_by_ocr,
                        "metadata": metadata,
                    }
                )
    if ocr:
        logger.info(
            "Figure table OCR: {} image figure(s) of {} read by RapidOCR, {} table(s) found",
            ocr_figures,
            Path(path).name,
            sum(item["read_by_ocr"] for item in found),
        )
    return found


def _detect(words: list[tuple], page: int) -> list[dict[str, Any]]:
    """Tables among the words; a failure on one figure must not fail the parse."""
    if not words:
        return []
    try:
        tables = _rows(words)
        used = {id(w) for table in tables for row in table["rows"] for w in row}
        return tables + _label_values([w for w in words if id(w) not in used])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Figure table detection failed on page {}: {}", page, exc)
        return []
