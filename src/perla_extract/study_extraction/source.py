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
from .spans import linked_context_closure, linked_context_ids

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

    return _sha256(Path(__file__))


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


def _contained_in(first: list[float], second: list[float]) -> bool:
    """Suppress native text only when its entire rectangle is inside a table.

    Even a mostly overlapping block may contain a unique result outside the table.
    Boundary differences therefore favor retaining text over losing evidence.
    """

    return (
        second[0] <= first[0] <= first[2] <= second[2]
        and second[1] <= first[1] <= first[3] <= second[3]
    )


def _pymupdf_layout_items(path: Path) -> list[dict[str, Any]]:
    """Recover native tables without discarding prose outside their rectangles.

    PyMuPDF exposes table cells and ordinary text through separate APIs, so accepting
    both naïvely duplicates the same values. Plausible multirow tables are emitted once,
    and text blocks fully contained in their rectangles are suppressed. Remaining
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
            for table in tables:
                rows = [
                    [_normalize_layout_text(cell) or None for cell in row]
                    for row in (table.extract() or [])
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
                if any(_contained_in(bbox, table_box) for table_box in table_boxes):
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


def _docling_bbox(item: Any, document: Any) -> list[float] | None:
    """Return PDF points from the top-left corner, or no geometry when unavailable.

    Docling provenance may use a bottom-left origin. Converting both vertical
    coordinates using that page's height preserves the rectangle's actual location;
    sorting the original numbers would instead mirror it. Reading order does not
    require geometry, so missing coordinates never acquire a synthetic rectangle.
    """

    provenance = getattr(item, "prov", None) or []
    if not provenance:
        return None
    bbox = getattr(provenance[0], "bbox", None)
    if bbox is None:
        return None
    page = document.pages.get(provenance[0].page_no)
    if page is None or page.size is None:
        return None
    converted = bbox.to_top_left_origin(page_height=page.size.height)
    return [float(getattr(converted, name)) for name in ("l", "t", "r", "b")]


def _docling_table(item: Any) -> tuple[str, dict[str, object]]:
    """Serialize source cells without dataframe-generated columns or merged headers.

    Docling's grid repeats an explicitly spanning cell in the positions it covers;
    an ordinary blank stays blank. Preserve source header rows separately instead
    of concatenating them into new column names. Logical cell spans are metadata,
    not source quantities, and never replace the cell text used for citations.
    """
    rows = [
        [" ".join(_normalize_layout_text(cell.text).split()) or None for cell in row]
        for row in item.data.grid
    ]
    layout = [
        {
            "row_start": cell.start_row_offset_idx,
            "row_end": cell.end_row_offset_idx,
            "column_start": cell.start_col_offset_idx,
            "column_end": cell.end_col_offset_idx,
            "column_header": cell.column_header,
            "row_header": cell.row_header,
            "row_section": cell.row_section,
        }
        for cell in item.data.table_cells
        if cell.row_span > 1
        or cell.col_span > 1
        or cell.column_header
        or cell.row_header
        or cell.row_section
    ]
    return "\n".join(" | ".join(cell or "" for cell in row) for row in rows), {
        "rows": rows,
        "table_layout": layout,
    }


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


def _parse_docling(path: Path, source: str) -> list[EvidenceBlock]:
    """Adapt Docling structure without leaking its object model downstream.

    Docling supplies reading order, headings, tables, provenance, and semantic content
    labels. The adapter converts those once into ordinary layout items and records which
    references or document furniture should be excluded from model evidence; filtering
    happens later so the complete parse remains cacheable and inspectable. Compact native
    typography blocks support subscripts and superscripts that conversion may normalize.
    """

    converter, ContentLayer, DocItemLabel = _docling_runtime()
    document = converter.convert(str(path)).document
    return _docling_evidence_blocks(
        document, path, source, content_layer_type=ContentLayer, item_label=DocItemLabel
    )


def _docling_evidence_blocks(
    document: Any,
    path: Path,
    source: str,
    *,
    content_layer_type: Any,
    item_label: Any,
) -> list[EvidenceBlock]:
    """Adapt a converted Docling document using the production evidence contract.

    Keeping conversion separate lets local parser experiments compare Docling
    pipelines without duplicating table, coordinate or context-link handling.
    Native typography is attached identically for every pipeline.
    """

    layout_items: list[dict[str, Any]] = []
    context_refs: dict[str, list[str]] = {}
    item_refs: list[str] = []
    items = document.iterate_items(
        included_content_layers={content_layer_type.BODY, content_layer_type.FURNITURE}
    )
    for item, hierarchy_level in items:
        label = _semantic_value(getattr(item, "label", ""))
        content_layer = _semantic_value(getattr(item, "content_layer", ""))
        class_name = type(item).__name__.casefold()
        provenance = getattr(item, "prov", None) or []
        page = int(getattr(provenance[0], "page_no", 1) or 1) if provenance else 1
        text = _normalize_layout_text(getattr(item, "text", ""))
        metadata: dict[str, object] = {
            "docling_label": label,
            "content_layer": content_layer,
            "include_in_evidence": not (
                getattr(item, "label", None)
                in {
                    item_label.REFERENCE,
                    item_label.PAGE_HEADER,
                    item_label.PAGE_FOOTER,
                }
                or getattr(item, "content_layer", None) == content_layer_type.FURNITURE
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
            text, table_metadata = _docling_table(item)
            metadata.update(table_metadata)
            context_refs[item.self_ref] = [
                ref.cref for ref in [*item.captions, *item.footnotes]
            ]
        if not text:
            continue
        bbox = _docling_bbox(item, document)
        item_refs.append(item.self_ref)
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
    block_by_ref = dict(zip(item_refs, blocks))
    for ref, context in context_refs.items():
        if ref in block_by_ref:
            block_by_ref[ref].metadata["context_block_ids"] = [
                block_by_ref[target].block_id
                for target in context
                if target in block_by_ref
            ]
    _attach_explicit_table_notes(blocks)
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
    return blocks


def _attach_explicit_table_notes(blocks: list[EvidenceBlock]) -> None:
    """Link a note that explicitly names one uniquely captioned table.

    Docling sometimes retains table notes as ordinary text. A complete heading
    such as ``Note to Table S1:`` establishes its target without a distance or
    publisher rule. Repeated table identifiers, unlinked captions, ordinary
    discussion references and notes without an explicit target remain unresolved.
    """

    label = r"(?:S\s*)?\d+[A-Za-z]?"
    caption_pattern = re.compile(rf"^Table\s+({label})\s*[.:]", re.I)
    note_pattern = re.compile(
        rf"^(?:Notes?\s+(?:to|for)\s+Table\s+({label})|"
        rf"Table\s+({label})\s+notes?)\s*[:.]\s*\S",
        re.I,
    )
    by_id = {block.block_id: block for block in blocks}
    targets: dict[tuple[str, str], list[EvidenceBlock]] = {}
    for block in blocks:
        if block.kind != "table":
            continue
        for identifier in linked_context_ids(block):
            caption = by_id.get(identifier)
            match = caption_pattern.match(caption.text) if caption else None
            if match:
                key = (block.source, re.sub(r"\s", "", match[1]).casefold())
                if block not in targets.setdefault(key, []):
                    targets[key].append(block)
    for note in blocks:
        if note.kind == "table" or (match := note_pattern.match(note.text)) is None:
            continue
        identifier = re.sub(r"\s", "", match[1] or match[2]).casefold()
        tables = targets.get((note.source, identifier), [])
        if len(tables) != 1:
            continue
        table = tables[0]
        linked = linked_context_ids(table)
        if note.block_id not in linked:
            table.metadata["context_block_ids"] = [*linked, note.block_id]
            table.metadata.setdefault("explicit_table_note_ids", []).append(
                note.block_id
            )


def available_parsers() -> list[str]:
    """List stable user-facing parser choices."""

    return ["docling", "pymupdf"]


def _scientific_evidence_blocks(
    blocks: list[EvidenceBlock],
) -> tuple[list[EvidenceBlock], int]:
    """Build the model-facing view from parser semantics, never heading names.

    Parsed blocks remain complete in the document cache. Only content that Docling
    explicitly classifies as references or document furniture is withheld unless
    a retained scientific block links it as required context. PyMuPDF and unknown
    Docling classifications pass through unchanged.
    """

    retained = linked_context_closure(
        blocks,
        (
            block.block_id
            for block in blocks
            if block.metadata.get("include_in_evidence", True) is not False
        ),
    )
    evidence = [block for block in blocks if block.block_id in retained]
    return evidence, len(blocks) - len(evidence)


def _cache_identity(
    *,
    source_hash: str,
    source: str,
    parser: str,
    dependency_versions: dict[str, str],
) -> dict[str, object]:
    """Describe all reproducibility-relevant inputs to a parsed document.

    Content bytes, main-versus-supplement provenance, backend and dependency version,
    adapter implementation, evidence schema, and cache envelope can all change the
    resulting blocks. File paths and modification times are excluded because they do not
    change scientific evidence.
    """

    return {
        "cache_format_version": DOCUMENT_CACHE_FORMAT_VERSION,
        "parser_implementation_sha256": _parser_implementation_sha256(),
        "evidence_schema_sha256": _evidence_schema_sha256(),
        "source_sha256": source_hash,
        "source": source,
        "parser": parser,
        "dependency_versions": dependency_versions,
    }


def _parser_dependency_versions(parser: str) -> dict[str, str]:
    """Fingerprint every installed package that contributes parser output.

    The Docling adapter supplements structural conversion with PyMuPDF typography,
    so either dependency can change the resulting evidence. The lightweight parser
    depends on PyMuPDF alone.
    """

    names = (
        ("docling", "docling-core", "PyMuPDF") if parser == "docling" else ("PyMuPDF",)
    )
    return {name: _package_version(name) for name in names}


def parse_pdf(
    path: Path,
    source: str,
    *,
    parser: str = "docling",
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    heartbeat_seconds: float = 20,
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
                _parse_docling(path, source)
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
    """Remove repeated SI prose while preserving tables and their context.

    Some downloaded supplements contain the article PDF concatenated before the actual
    SI. Long normalized blocks that exactly match the main paper keep the main-paper
    instance as canonical and record the skipped SI location in its metadata. Equal
    table grids can describe different measurements, so tables, explicit context
    links and their targets are never deduplicated. Short prose, near matches and
    duplicates within one source are retained to avoid recall loss.
    """

    protected = {
        identifier for block in blocks for identifier in linked_context_ids(block)
    }
    protected.update(
        block.block_id
        for block in blocks
        if block.kind not in {"text", "paragraph"}
        or linked_context_ids(block)
        or block.metadata.get("docling_label") in {"caption", "footnote"}
    )
    main: dict[str, EvidenceBlock] = {}
    for block in blocks:
        if block.source != "main" or block.block_id in protected:
            continue
        key = _normalized_block_text_key(block)
        if len(key) > 40:
            main[key] = block
    output: list[EvidenceBlock] = []
    skipped = 0
    for block in blocks:
        key = _normalized_block_text_key(block)
        canonical = (
            main.get(key)
            if block.source != "main" and block.block_id not in protected
            else None
        )
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
) -> tuple[list[EvidenceBlock], list[dict[str, object]]]:
    """Build one evidence stream from a main article and optional supplement.

    Both documents use the same selected backend but retain distinct ``main`` and
    ``supplement`` provenance in every block and parse event. After parsing, only exact
    unlinked cross-document prose repetitions are collapsed. The returned events expose cache use,
    parser versions, source hashes, exclusions, and deduplication for the run manifest.
    """

    blocks, main_event = parse_pdf(
        pdf,
        "main",
        parser=parser,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
        heartbeat_seconds=heartbeat_seconds,
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
        )
        blocks.extend(supplement_blocks)
        events.append(supplement_event)
    blocks, skipped = _deduplicate_evidence_blocks(blocks)
    events.append({"operation": "cross_document_deduplication", "skipped": skipped})
    return blocks, events
