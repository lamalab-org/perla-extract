"""Parse PDFs into a small, parser-independent evidence representation.

The extractor consumes ordered blocks rather than parser-specific document
objects. Docling is the quality-first default; PyMuPDF remains an explicit,
lightweight alternative. Both preserve page locations, section context, tables,
and source wording.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

import pymupdf
from pydantic import ValidationError

from .artifacts import write_json_atomic
from .logging import logger
from .models import EvidenceBlock
from .progress import heartbeat

# Bump only when the cache envelope itself becomes incompatible. Parser code,
# block schema, source, and backend changes are fingerprinted automatically.
DOCUMENT_CACHE_FORMAT_VERSION = 1


def _package_version(name: str) -> str:
    """Read the parser dependency version that forms part of the cache identity.

    Parser upgrades can change ordering, tables, or text without changing our code.
    The explicit missing marker prevents such an environment from sharing a cache key
    with an installed but unknown release; ``parse_pdf`` then reports the missing
    dependency before parsing.
    """

    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _sha256(path: Path) -> str:
    """Hash a source file without loading a long supplement into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _parser_implementation_sha256() -> str:
    """Fingerprint parser behavior so code edits cannot reuse stale parses.

    Hashing the module may invalidate a cache after a harmless refactor, but it is
    safer than relying on a manually bumped version.
    """

    digest = hashlib.sha256()
    for module in (Path(__file__), Path(__file__).with_name("figure_tables.py")):
        digest.update(_sha256(module).encode("ascii"))
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _evidence_schema_sha256() -> str:
    """Invalidate parses when the serialized ``EvidenceBlock`` contract changes.

    A parser can return the same text after a schema change while its cached JSON is no
    longer a valid model input. Keeping this fingerprint separate from the cache-format
    version means ordinary schema edits do not require a manual version bump.
    """

    schema = json.dumps(
        EvidenceBlock.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(schema).hexdigest()


def _semantic_value(value: object) -> str:
    """Compare parser labels by meaning rather than by release-specific Python type.

    Docling has exposed labels as enums and strings across releases. Converting either
    representation to the same lowercase underscore form keeps the adapter tolerant of
    those API changes without teaching downstream extraction code about Docling.
    """

    raw = getattr(value, "value", value)
    return re.sub(r"[-\s]+", "_", str(raw or "").strip().casefold())


def _block_id(source: str, page: int, order: int, kind: str, text: str) -> str:
    """Give a parsed block a reproducible citation identifier.

    Source, page, reading order, and kind make the location intelligible; the text hash
    prevents changed parser output from retaining an old citation identity. The ID is
    therefore stable across serialization but intentionally changes when evidence does.
    """

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{source}_p{page}_{order:04d}_{kind}_{digest}"


def _normalize_layout_text(value: object) -> str:
    """Remove PDF-layout spacing noise without flattening meaningful rows and lines.

    Repeated horizontal whitespace is rarely semantic in parser output, while line
    boundaries remain useful for tables, formulas, and evidence review. Empty layout
    fragments are discarded so they do not become standalone evidence.
    """

    lines = [
        re.sub(r"[ \t]+", " ", line).strip() for line in str(value or "").splitlines()
    ]
    return "\n".join(line for line in lines if line)


def _formatted_line(line: dict[str, Any]) -> str:
    """Represent probable subscript and superscript glyphs using PDF geometry.

    Plain PDF text often turns chemical formulas such as raised charges or lowered
    stoichiometric indices into an ambiguous character sequence. Smaller spans above or
    below the dominant baseline are marked as ``^{...}`` or ``_{...}``. This rendering
    supplements the untouched plain text; it never silently replaces the source text.
    """

    spans = [span for span in line.get("spans", []) if span.get("text")]
    if not spans:
        return ""
    largest = max(float(span.get("size", 0)) for span in spans)
    normal = [span for span in spans if float(span.get("size", 0)) >= largest * 0.85]
    baseline = median(float(span.get("origin", (0, 0))[1]) for span in normal)
    tolerance = largest * 0.12
    parts: list[tuple[str | None, str]] = []
    for span in spans:
        text = str(span.get("text", ""))
        style = None
        if float(span.get("size", 0)) < largest * 0.85 and text.strip():
            offset = float(span.get("origin", (0, 0))[1]) - baseline
            if offset > tolerance:
                style = "_"
            elif offset < -tolerance:
                style = "^"
        if parts and style is not None and parts[-1][0] == style:
            parts[-1] = (style, parts[-1][1] + text)
        else:
            parts.append((style, text))
    return "".join(
        text if style is None else f"{style}{{{text}}}" for style, text in parts
    )


def _overlap_fraction(first: list[float], second: list[float]) -> float:
    """Measure the fraction of a text rectangle covered by a detected table.

    The measure is deliberately asymmetric: it answers whether the first rectangle is
    already represented by the second. It is used only to suppress duplicated table
    text, not as a general geometric similarity score.
    """

    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(1.0, first[2] - first[0]) * max(1.0, first[3] - first[1])
    return intersection / area


def _visual_lines(words: list[tuple]) -> list[list[tuple]]:
    """Group words into printed lines by vertical overlap, top to bottom.

    A word joins a line when it overlaps the line's vertical extent by at least half
    of the smaller height, so raised or lowered glyphs (affiliation superscripts,
    subscripts) stay on the line they belong to rather than forming lines of their own.
    """

    lines: list[tuple[float, float, list[tuple]]] = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        top, bottom = word[1], word[3]
        for index, (line_top, line_bottom, members) in enumerate(lines):
            overlap = min(bottom, line_bottom) - max(top, line_top)
            smaller = min(bottom - top, line_bottom - line_top)
            if smaller > 0 and overlap >= 0.5 * smaller:
                members.append(word)
                lines[index] = (min(top, line_top), max(bottom, line_bottom), members)
                break
        else:
            lines.append((top, bottom, [word]))
    return [members for _, _, members in sorted(lines, key=lambda line: line[0])]


def _table_cell_texts(
    table: Any, page_words: list[tuple]
) -> list[list[str | None]]:
    """Read table cells from the page's words instead of ``Table.extract()``.

    ``extract()`` groups characters into lines using tight glyph boxes, so a decimal
    point, which sits lower than the digits, can be split onto its own line
    (``26.38±0.12`` becomes ``2638±012`` and ``. .``). PyMuPDF's word extraction keeps
    them together. Each word goes to the cell containing its centre; words are grouped
    into printed lines and ordered top to bottom, left to right. Merged
    cells stay ``None`` as in ``extract()``, and a cell that receives no word falls
    back to ``extract()`` so no text is lost.
    """

    extracted = table.extract() or []
    try:
        cell_rows = [row.cells for row in table.rows]
    except Exception:  # noqa: BLE001 - row geometry is optional across PyMuPDF releases
        return extracted
    if len(cell_rows) != len(extracted):
        return extracted
    result: list[list[str | None]] = []
    for row_index, cells in enumerate(cell_rows):
        fallback_row = extracted[row_index]
        row: list[str | None] = []
        for column, bbox in enumerate(cells):
            fallback = fallback_row[column] if column < len(fallback_row) else None
            if bbox is None:
                row.append(fallback)
                continue
            x0, y0, x1, y1 = bbox
            inside = [
                word
                for word in page_words
                if x0 <= (word[0] + word[2]) / 2 <= x1
                and y0 <= (word[1] + word[3]) / 2 <= y1
            ]
            if not inside:
                row.append(fallback)
                continue
            row.append(
                "\n".join(
                    " ".join(w[4] for w in sorted(line, key=lambda w: w[0]))
                    for line in _visual_lines(inside)
                )
            )
        result.append(row)
    return result


def _pymupdf_layout_items(path: Path) -> list[dict[str, Any]]:
    """Recover native text and tables without emitting table contents twice.

    PyMuPDF exposes table cells and ordinary text through separate APIs, so accepting
    both naïvely duplicates the same values. Plausible multirow tables are emitted once,
    and text blocks substantially covered by their rectangles are suppressed. Remaining
    items retain native geometry and typography so later reading order, headings, and
    citations remain reproducible. Table-detection failure degrades to ordinary text
    rather than failing the complete paper.
    """

    layout_items: list[dict[str, Any]] = []
    with pymupdf.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            table_boxes: list[list[float]] = []
            try:
                tables = page.find_tables(strategy="lines_strict").tables
            except Exception as exc:  # noqa: BLE001 - optional detector failures vary by PyMuPDF release
                logger.debug("Table detection failed on page {}: {}", page_index, exc)
                tables = []
            page_words = page.get_text("words") if tables else []
            for table in tables:
                rows = [
                    [_normalize_layout_text(cell) or None for cell in row]
                    for row in _table_cell_texts(table, page_words)
                ]
                nonempty_by_row = [
                    sum(bool(cell) for cell in row) for row in rows if row
                ]
                if (
                    sum(nonempty_by_row) < 4
                    or sum(count >= 2 for count in nonempty_by_row) < 2
                ):
                    continue
                text = "\n".join(
                    " | ".join(cell or "" for cell in row) for row in rows if row
                )
                if not text:
                    continue
                bbox = [float(value) for value in table.bbox]
                native_text = page.get_text("text", clip=pymupdf.Rect(bbox), sort=True)
                if not _table_preserves_numbers(text, native_text):
                    # Leave the native region in the ordinary-text path below.
                    # Never infer where a detached decimal should have been.
                    logger.debug(
                        "Retaining native text for table with changed numeric tokens on page {}",
                        page_index,
                    )
                    continue
                table_boxes.append(bbox)
                layout_items.append(
                    {
                        "page": page_index,
                        "bbox": bbox,
                        "kind": "table",
                        "text": text,
                        "font_size": 0.0,
                        "bold_fraction": 0.0,
                        "metadata": {"rows": rows},
                    }
                )

            page_dict = page.get_text("dict", sort=True)
            for raw_block in page_dict.get("blocks", []):
                if raw_block.get("type") != 0:
                    continue
                bbox = [float(value) for value in raw_block.get("bbox", (0, 0, 0, 0))]
                if any(
                    _overlap_fraction(bbox, table_box) > 0.55
                    for table_box in table_boxes
                ):
                    continue
                lines = raw_block.get("lines", [])
                plain_lines = [
                    "".join(str(span.get("text", "")) for span in line.get("spans", []))
                    for line in lines
                ]
                plain = _normalize_layout_text("\n".join(plain_lines))
                if not plain:
                    continue
                formatted = _normalize_layout_text(
                    "\n".join(_formatted_line(line) for line in lines)
                )
                text = plain
                if formatted and formatted != plain:
                    text += "\nTypography-preserving rendering: " + formatted
                spans = [span for line in lines for span in line.get("spans", [])]
                weighted_sizes = [
                    float(span.get("size", 0))
                    for span in spans
                    for _ in range(min(40, max(1, len(str(span.get("text", ""))))))
                ]
                bold_characters = sum(
                    len(str(span.get("text", "")))
                    for span in spans
                    if "bold" in str(span.get("font", "")).casefold()
                )
                total_characters = max(
                    1, sum(len(str(span.get("text", ""))) for span in spans)
                )
                layout_items.append(
                    {
                        "page": page_index,
                        "bbox": bbox,
                        "kind": "text",
                        "text": text,
                        "font_size": median(weighted_sizes) if weighted_sizes else 0.0,
                        "max_font_size": max(weighted_sizes) if weighted_sizes else 0.0,
                        "bold_fraction": bold_characters / total_characters,
                        "metadata": {},
                    }
                )
    return layout_items


def _table_preserves_numbers(table_text: str, native_text: str) -> bool:
    """Reject table renderings that alter native numbers or their multiplicity.

    This is a loss detector, not a reconstruction or a guarantee of row alignment.
    Native text remains available whenever the table detector changes numeric
    tokens, including splitting a decimal point onto another line.
    """
    pattern = r"[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?"

    def numbers(text: str) -> Counter[str]:
        return Counter(re.findall(pattern, text.replace("−", "-")))

    return numbers(table_text) == numbers(native_text)


def _classify_headings(layout_items: list[dict[str, Any]]) -> None:
    """Infer headings from typography without maintaining publisher-specific names.

    Native PDF text has no reliable section type. Short, non-sentence blocks that are
    larger or substantially bolder than estimated body text are treated as headings;
    relative font sizes provide hierarchy levels. The heuristic supplies section context
    for evidence navigation, not scientific filtering, so a missed heading cannot remove
    source content.
    """

    body_sizes = [
        item["font_size"]
        for item in layout_items
        if item["kind"] == "text" and len(item["text"]) >= 120 and item["font_size"] > 0
    ]
    body_size = median(body_sizes) if body_sizes else 10.0
    heading_sizes: list[float] = [
        round(float(item.get("max_font_size") or item.get("font_size") or 0), 1)
        for item in layout_items
        if item["kind"] == "heading" and "level" not in item
    ]
    for item in layout_items:
        if item["kind"] != "text":
            continue
        compact = item["text"].split("\nTypography-preserving rendering:", 1)[0]
        short = len(compact) <= 220 and compact.count("\n") <= 3
        ends_sentence = bool(re.search(r"[.!?;]$", compact))
        large = item.get("max_font_size", 0) >= body_size * 1.16
        bold = (
            item.get("bold_fraction", 0) >= 0.72
            and item["font_size"] >= body_size * 0.98
        )
        if short and not ends_sentence and (large or bold):
            item["kind"] = "heading"
            heading_sizes.append(
                round(float(item.get("max_font_size") or item["font_size"]), 1)
            )
    size_order = sorted(set(heading_sizes), reverse=True)
    for item in layout_items:
        if item["kind"] == "heading" and "level" not in item:
            size = round(float(item.get("max_font_size") or item["font_size"]), 1)
            item["level"] = min(size_order.index(size) + 1, 6)


def _evidence_blocks_from_layout(
    layout_items: list[dict[str, Any]], source: str, *, sort_geometry: bool = True
) -> list[EvidenceBlock]:
    """Convert backend layout items into the parser-neutral evidence contract.

    The current heading stack is copied onto each block as its section path, while page,
    geometry, wording, and backend metadata remain attached to the block itself. Optional
    geometry sorting is used for native PDFs; structured backends such as Docling can
    preserve the reading order they already computed.
    """

    _classify_headings(layout_items)
    if sort_geometry:
        layout_items.sort(
            key=lambda item: (item["page"], item["bbox"][1], item["bbox"][0])
        )
    headings: list[tuple[int, str]] = []
    blocks: list[EvidenceBlock] = []
    for order, item in enumerate(layout_items, start=1):
        if item["kind"] == "heading":
            level = int(item.get("level", 1))
            while headings and headings[-1][0] >= level:
                headings.pop()
            heading_text = item["text"].split("\nTypography-preserving rendering:", 1)[
                0
            ]
            headings.append((level, heading_text))
        section_path = [heading for _, heading in headings]
        block = EvidenceBlock(
            block_id=_block_id(source, item["page"], order, item["kind"], item["text"]),
            source=source,
            page=item["page"],
            section_path=section_path,
            kind=item["kind"],
            text=item["text"],
            bbox=item["bbox"],
            metadata=item.get("metadata", {}),
        )
        blocks.append(block)
    return blocks


def _parse_pymupdf(path: Path, source: str) -> list[EvidenceBlock]:
    """Parse a born-digital PDF with the lightweight native-text backend.

    This path is useful when Docling is unavailable or its extra layout processing is
    unnecessary. It extracts embedded text and tables but deliberately does not pretend
    to OCR image-only pages; missing source text remains visible as a parser limitation.
    """

    return _evidence_blocks_from_layout(_pymupdf_layout_items(path), source)


def _docling_bbox(item: object) -> list[float] | None:
    """Translate optional Docling provenance into the common rectangle representation.

    Attribute access rather than concrete Docling classes keeps this narrow adapter
    compatible across releases. Returning ``None`` is explicit: the caller can provide
    deterministic ordering geometry without inventing source coordinates.
    """

    provenance = getattr(item, "prov", None) or []
    if not provenance:
        return None
    bbox = getattr(provenance[0], "bbox", None)
    values = [getattr(bbox, name, None) for name in ("l", "t", "r", "b")]
    return (
        [float(value) for value in values if value is not None]
        if all(value is not None for value in values)
        else None
    )


def _native_typography_blocks(
    path: Path,
    source: str,
    start_order: int,
    included_pages: set[int] | None = None,
) -> list[EvidenceBlock]:
    """Add native typography evidence where structural conversion may lose chemistry.

    Docling provides the main document structure, but native PDF glyph positions can
    preserve formula indices and charges that normalized text loses. Only differing
    formatted lines from pages containing scientific evidence are added, keeping this a
    compact supporting source rather than a duplicate parse of the paper.
    """

    blocks: list[EvidenceBlock] = []
    with pymupdf.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            if included_pages is not None and page_index not in included_pages:
                continue
            fragments: list[str] = []
            for raw_block in page.get_text("dict", sort=True).get("blocks", []):
                for line in raw_block.get("lines", []):
                    plain = "".join(
                        str(span.get("text", "")) for span in line.get("spans", [])
                    )
                    formatted = _formatted_line(line)
                    if formatted and formatted != plain:
                        fragments.append(formatted)
            if not fragments:
                continue
            text = "Typography-preserving source fragments:\n" + "\n".join(fragments)
            order = start_order + len(blocks)
            blocks.append(
                EvidenceBlock(
                    block_id=_block_id(source, page_index, order, "typography", text),
                    source=source,
                    page=page_index,
                    section_path=[],
                    kind="typography",
                    text=text,
                    bbox=None,
                    metadata={"source_kind": "native_pdf_typography"},
                )
            )
    return blocks


@lru_cache(maxsize=1)
def _docling_runtime() -> tuple[Any, Any, Any]:
    """Load Docling's converter and local layout models once per batch process.

    A converter owns large ML models. Recreating it for every paper and supplement is
    expensive and can trigger needless remote registry checks even though extraction
    is sequential. Returning the Docling enums with it keeps those dependency-specific
    objects inside this adapter.
    """

    try:
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import ContentLayer, DocItemLabel
    except ImportError as exc:
        raise RuntimeError("Docling is not installed; reinstall perla-extract") from exc
    return DocumentConverter(), ContentLayer, DocItemLabel


def _parse_docling(
    path: Path,
    source: str,
    *,
    figure_table_parsing: bool = False,
    figure_table_ocr_dpi: int = 300,
) -> list[EvidenceBlock]:
    """Adapt Docling structure without leaking its object model downstream.

    Docling supplies reading order, headings, tables, provenance, and semantic content
    labels. The adapter converts those once into ordinary layout items and records which
    references or document furniture should be excluded from model evidence; filtering
    happens later so the complete parse remains cacheable and inspectable. Compact native
    typography blocks support subscripts and superscripts that conversion may normalize.

    With ``figure_table_parsing``, numeric tables printed inside figures are rebuilt
    from PyMuPDF's words within Docling's figure regions (see ``figure_tables``). They
    are placed after their figure's caption, but numbered after all other blocks, so
    existing block identifiers stay unchanged. Image figures without a usable text
    layer are read by RapidOCR instead, rendered at ``figure_table_ocr_dpi``.
    """

    converter, ContentLayer, DocItemLabel = _docling_runtime()
    document = converter.convert(str(path)).document
    layout_items: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    items = document.iterate_items(
        included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
    )
    for item, hierarchy_level in items:
        label = _semantic_value(getattr(item, "label", ""))
        content_layer = _semantic_value(getattr(item, "content_layer", ""))
        class_name = type(item).__name__.casefold()
        provenance = getattr(item, "prov", None) or []
        page = int(getattr(provenance[0], "page_no", 1) or 1) if provenance else 1
        text = _normalize_layout_text(getattr(item, "text", ""))
        if figure_table_parsing and (
            "picture" in class_name or label in {"picture", "chart"}
        ):
            box = getattr(provenance[0], "bbox", None) if provenance else None
            caption = ""
            for reference in getattr(item, "captions", None) or []:
                try:
                    caption = _normalize_layout_text(reference.resolve(document).text)
                    break
                except Exception:  # noqa: BLE001 - caption links vary by Docling release
                    continue
            if box is not None:
                figures.append(
                    {
                        "page": max(1, page),
                        "bbox": [float(box.l), float(box.t), float(box.r), float(box.b)],
                        "bottom_left_origin": "BOTTOM"
                        in str(getattr(box, "coord_origin", "BOTTOMLEFT")).upper(),
                        "after": len(layout_items),
                        "caption": caption,
                    }
                )
        metadata: dict[str, object] = {
            "docling_label": label,
            "content_layer": content_layer,
            "include_in_evidence": not (
                getattr(item, "label", None)
                in {
                    DocItemLabel.REFERENCE,
                    DocItemLabel.PAGE_HEADER,
                    DocItemLabel.PAGE_FOOTER,
                }
                or getattr(item, "content_layer", None) == ContentLayer.FURNITURE
            ),
        }
        kind = "text"
        if (
            "sectionheader" in class_name
            or "section_header" in label
            or label.endswith("title")
        ):
            kind = "heading"
        elif "table" in class_name or label.endswith("table"):
            kind = "table"
            try:
                dataframe = item.export_to_dataframe(doc=document)
                rows = [
                    [
                        _normalize_layout_text(value) or None
                        for value in dataframe.columns.tolist()
                    ],
                    *[
                        [_normalize_layout_text(value) or None for value in row]
                        for row in dataframe.astype(object)
                        .where(dataframe.notna(), None)
                        .values.tolist()
                    ],
                ]
                text = "\n".join(" | ".join(cell or "" for cell in row) for row in rows)
                metadata["rows"] = rows
            except Exception as exc:  # noqa: BLE001 - Docling APIs vary by release
                logger.debug("Docling table export failed: {}", exc)
        if not text:
            continue
        bbox = _docling_bbox(item) or [
            0.0,
            float(len(layout_items)),
            0.0,
            float(len(layout_items) + 1),
        ]
        layout_items.append(
            {
                "page": max(1, page),
                "bbox": bbox,
                "kind": kind,
                "text": text,
                "font_size": 0.0,
                "max_font_size": 0.0,
                "bold_fraction": 0.0,
                "level": max(1, int(hierarchy_level or 1)),
                "metadata": metadata,
            }
        )
    # Docling already classifies headings. Avoid replacing its labels by giving
    # non-heading layout_items no typography signal in _evidence_blocks_from_layout.
    blocks = _evidence_blocks_from_layout(layout_items, source, sort_geometry=False)
    scientific_pages = {
        block.page
        for block in blocks
        if block.kind != "heading"
        and block.metadata.get("include_in_evidence", True) is not False
    }
    blocks.extend(
        _native_typography_blocks(
            path, source, len(blocks) + 1, included_pages=scientific_pages
        )
    )
    if figure_table_parsing:
        if figures:
            pages = sorted({figure["page"] for figure in figures})
            logger.info(
                "Figure table parsing: PyMuPDF reads {} Docling figure(s) of {} (page(s) {})",
                len(figures),
                path.name,
                ", ".join(map(str, pages)),
            )
            blocks = _with_figure_tables(
                path, source, figures, blocks, ocr=True, ocr_dpi=figure_table_ocr_dpi
            )
        else:
            logger.info(
                "Figure table parsing: Docling found no figures in {}; PyMuPDF not applied",
                path.name,
            )
    return blocks


def _with_figure_tables(
    path: Path,
    source: str,
    figures: list[dict[str, Any]],
    blocks: list[EvidenceBlock],
    *,
    ocr: bool = False,
    ocr_dpi: int = 300,
) -> list[EvidenceBlock]:
    """Insert figure-table blocks after their figure's caption.

    Reading order follows the position in the block list, so each table sits next to
    its figure. Its identifier is numbered after all existing blocks, so inserting it
    renumbers nothing. The section path names the figure (from its caption), so the
    link survives even where a reading window starts at the table.
    """
    from .figure_tables import figure_tables, section_label

    placed: list[tuple[int, EvidenceBlock]] = []
    order = len(blocks)
    figure_indices: set[int] = set()
    for item in figure_tables(path, figures, ocr=ocr, ocr_dpi=ocr_dpi):
        figure_indices.add(item["figure_index"])
        figure = figures[item["figure_index"]]
        position = min(figure["after"], len(blocks))  # where the figure itself would be
        caption = figure.get("caption") or ""
        for index, block in enumerate(blocks):
            if caption and block.page == figure["page"] and block.text.startswith(caption[:60]):
                position = index + 1
                break
        section = list(blocks[position - 1].section_path) if position > 0 else []
        order += 1
        placed.append(
            (
                position,
                EvidenceBlock(
                    block_id=_block_id(source, item["page"], order, "figure_table", item["text"]),
                    source=source,
                    page=item["page"],
                    section_path=[
                        *section[-19:],
                        section_label(caption, ocr=item.get("read_by_ocr", False)),
                    ],
                    kind="figure_table",
                    text=item["text"],
                    bbox=item["bbox"],
                    metadata={**item["metadata"], "figure_caption_start": caption[:80] or None},
                ),
            )
        )
    logger.info(
        "Figure table parsing: {} table(s) rebuilt in {} of {} figure(s) of {}{}",
        len(placed),
        len(figure_indices),
        len(figures),
        path.name,
        f" (page(s) {', '.join(str(b.page) for _, b in placed)})" if placed else "",
    )
    result = list(blocks)
    for position, block in sorted(placed, key=lambda entry: entry[0], reverse=True):
        result.insert(position, block)
    return result


def available_parsers() -> list[str]:
    """List stable user-facing parser choices."""

    return ["docling", "pymupdf"]


def _scientific_evidence_blocks(
    blocks: list[EvidenceBlock],
) -> tuple[list[EvidenceBlock], int]:
    """Build the model-facing view from parser semantics, never heading names.

    Parsed blocks remain complete in the document cache. Only content that Docling
    explicitly classifies as references or document furniture is withheld. PyMuPDF
    and unknown Docling classifications pass through unchanged.
    """

    evidence: list[EvidenceBlock] = []
    for block in blocks:
        if block.metadata.get("include_in_evidence", True) is False:
            continue
        evidence.append(block)
    return evidence, len(blocks) - len(evidence)


def _cache_identity(
    *,
    source_hash: str,
    source: str,
    parser: str,
    dependency_versions: dict[str, str],
    figure_table_parsing: bool = False,
    figure_table_ocr_dpi: int = 300,
) -> dict[str, object]:
    """Describe all reproducibility-relevant inputs to a parsed document.

    Content bytes, main-versus-supplement provenance, backend and dependency version,
    adapter implementation, evidence schema, and cache envelope can all change the
    resulting blocks. File paths and modification times are excluded because they do not
    change scientific evidence.
    """

    identity: dict[str, object] = {
        "cache_format_version": DOCUMENT_CACHE_FORMAT_VERSION,
        "parser_implementation_sha256": _parser_implementation_sha256(),
        "evidence_schema_sha256": _evidence_schema_sha256(),
        "source_sha256": source_hash,
        "source": source,
        "parser": parser,
        "dependency_versions": dependency_versions,
    }
    if figure_table_parsing:  # only when enabled, so default cache keys are unchanged
        identity["figure_table_parsing"] = True
        identity["rapidocr"] = _package_version("rapidocr")  # reads image figures
        identity["figure_table_ocr_dpi"] = figure_table_ocr_dpi
    return identity


def _parser_dependency_versions(parser: str) -> dict[str, str]:
    """Fingerprint every installed package that contributes parser output.

    The Docling adapter supplements structural conversion with PyMuPDF typography,
    so either dependency can change the resulting evidence. The lightweight parser
    depends on PyMuPDF alone.
    """

    names = ("docling", "PyMuPDF") if parser == "docling" else ("PyMuPDF",)
    return {name: _package_version(name) for name in names}


def parse_pdf(
    path: Path,
    source: str,
    *,
    parser: str = "docling",
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    heartbeat_seconds: float = 20,
    figure_table_parsing: bool = False,
    figure_table_ocr_dpi: int = 300,
) -> tuple[list[EvidenceBlock], dict[str, object]]:
    """Parse one PDF with the explicitly selected, reproducible backend.

    The cache retains the complete parse. The returned model-facing view omits only
    content that the backend confidently labels as references or document furniture.
    Cache identity covers the source, backend, dependency, block schema, and parser
    implementation.
    """

    if parser not in available_parsers():
        raise ValueError(
            f"Unknown parser {parser!r}; choose from {available_parsers()}"
        )
    if figure_table_parsing and parser != "docling":
        raise ValueError("figure_table_parsing requires the docling parser")
    low, high = 72, 1200
    if type(figure_table_ocr_dpi) is not int or not low <= figure_table_ocr_dpi <= high:
        raise ValueError(f"figure_table_ocr_dpi must be an integer from {low} to {high}")
    source_hash = _sha256(path)
    dependency_versions = _parser_dependency_versions(parser)
    if "not-installed" in dependency_versions.values():
        missing = ", ".join(
            name
            for name, version in dependency_versions.items()
            if version == "not-installed"
        )
        raise RuntimeError(
            f"The selected {parser} parser requires {missing}; reinstall perla-extract"
        )
    identity = _cache_identity(
        source_hash=source_hash,
        source=source,
        parser=parser,
        dependency_versions=dependency_versions,
        figure_table_parsing=figure_table_parsing,
        figure_table_ocr_dpi=figure_table_ocr_dpi,
    )
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cache_path = cache_dir / f"{key}.json" if cache_dir else None
    cache_hit = False
    parsed_blocks: list[EvidenceBlock] | None = None
    if cache_path and cache_path.exists() and not refresh_cache:
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if payload["cache_identity"] != identity:
                raise ValueError("cache identity does not match the current parser")
            parsed_blocks = [
                EvidenceBlock.model_validate(item) for item in payload["blocks"]
            ]
        except (
            OSError,
            TypeError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
            ValidationError,
        ):
            logger.warning("Ignoring invalid document cache entry {}", cache_path.name)
        else:
            cache_hit = True

    if parsed_blocks is None:
        logger.info("Parsing {} with {}", path.name, parser)
        with heartbeat(f"{parser} parsing for {path.name}", heartbeat_seconds):
            parsed_blocks = (
                _parse_docling(
                    path,
                    source,
                    figure_table_parsing=figure_table_parsing,
                    figure_table_ocr_dpi=figure_table_ocr_dpi,
                )
                if parser == "docling"
                else _parse_pymupdf(path, source)
            )
        if cache_path:
            write_json_atomic(
                cache_path,
                {
                    "cache_identity": identity,
                    "blocks": [
                        block.model_dump(mode="json") for block in parsed_blocks
                    ],
                },
            )

    if cache_hit and figure_table_parsing:
        logger.info(
            "Figure table parsing: {} figure table(s) loaded from the parse cache for {}",
            sum(block.kind == "figure_table" for block in parsed_blocks),
            path.name,
        )
    evidence_blocks, excluded = _scientific_evidence_blocks(parsed_blocks)
    logger.info(
        "{} {} with {} ({} evidence blocks, {} excluded blocks)",
        "Loaded" if cache_hit else "Parsed",
        path.name,
        parser,
        len(evidence_blocks),
        excluded,
    )
    return evidence_blocks, {
        "source": source,
        "source_path": str(path),
        "source_sha256": source_hash,
        "parser": parser,
        "parser_version": dependency_versions[
            "docling" if parser == "docling" else "PyMuPDF"
        ],
        "dependency_versions": dependency_versions,
        "parser_implementation_sha256": identity["parser_implementation_sha256"],
        "evidence_schema_sha256": identity["evidence_schema_sha256"],
        "cache_format_version": DOCUMENT_CACHE_FORMAT_VERSION,
        "figure_table_parsing": figure_table_parsing,
        **(
            {
                "rapidocr_version": identity["rapidocr"],
                "figure_table_ocr_dpi": figure_table_ocr_dpi,
            }
            if figure_table_parsing
            else {}
        ),
        "figure_table_ocr_blocks": sum(
            block.kind == "figure_table"
            and block.metadata.get("derived_from", "").endswith("rapidocr_words")
            for block in evidence_blocks
        ),
        "figure_table_blocks": sum(block.kind == "figure_table" for block in evidence_blocks),
        "cache_hit": cache_hit,
        "parsed_block_count": len(parsed_blocks),
        "excluded_block_count": excluded,
        "block_count": len(evidence_blocks),
    }


def _normalized_block_text_key(block: EvidenceBlock) -> str:
    """Create a conservative equality key for complete-block deduplication.

    Ignore repeated horizontal whitespace and line-ending conventions only. Case,
    Unicode scientific notation, and table-row boundaries remain significant: Co is
    not CO, and a superscript is not an ordinary digit.
    """

    text = block.text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def _deduplicate_evidence_blocks(
    blocks: list[EvidenceBlock],
) -> tuple[list[EvidenceBlock], int]:
    """Remove only full SI blocks that repeat a main-paper block verbatim.

    Some downloaded supplements contain the article PDF concatenated before the actual
    SI. Long normalized blocks that exactly match the main paper keep the main-paper
    instance as canonical and record the skipped SI location in its metadata. Short
    boilerplate, near matches, unique SI evidence, and duplicates within one source are
    retained to avoid recall loss.
    """

    main: dict[str, EvidenceBlock] = {}
    for block in blocks:
        if block.source != "main":
            continue
        key = _normalized_block_text_key(block)
        if len(key) > 40:
            main[key] = block
    output: list[EvidenceBlock] = []
    skipped = 0
    for block in blocks:
        key = _normalized_block_text_key(block)
        canonical = main.get(key) if block.source != "main" else None
        if canonical is None:
            output.append(block)
            continue
        locations = canonical.metadata.setdefault("duplicate_locations", [])
        if isinstance(locations, list):
            locations.append(
                {"source": block.source, "page": block.page, "block_id": block.block_id}
            )
        skipped += 1
    return output, skipped


def parse_documents(
    pdf: Path,
    supplement: Path | None = None,
    *,
    parser: str = "docling",
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    heartbeat_seconds: float = 20,
    figure_table_parsing: bool = False,
    figure_table_ocr_dpi: int = 300,
) -> tuple[list[EvidenceBlock], list[dict[str, object]]]:
    """Build one evidence stream from a main article and optional supplement.

    Both documents use the same selected backend but retain distinct ``main`` and
    ``supplement`` provenance in every block and parse event. After parsing, only exact
    cross-document repetitions are collapsed. The returned events expose cache use,
    parser versions, source hashes, exclusions, and deduplication for the run manifest.
    """

    blocks, main_event = parse_pdf(
        pdf,
        "main",
        parser=parser,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
        heartbeat_seconds=heartbeat_seconds,
        figure_table_parsing=figure_table_parsing,
        figure_table_ocr_dpi=figure_table_ocr_dpi,
    )
    events = [main_event]
    if supplement:
        supplement_blocks, supplement_event = parse_pdf(
            supplement,
            "supplement",
            parser=parser,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
            heartbeat_seconds=heartbeat_seconds,
            figure_table_parsing=figure_table_parsing,
            figure_table_ocr_dpi=figure_table_ocr_dpi,
        )
        blocks.extend(supplement_blocks)
        events.append(supplement_event)
    blocks, skipped = _deduplicate_evidence_blocks(blocks)
    events.append({"operation": "cross_document_deduplication", "skipped": skipped})
    return blocks, events
