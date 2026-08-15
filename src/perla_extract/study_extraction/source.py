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
import unicodedata
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
    """Return a dependency version for cache invalidation."""

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
    """Fingerprint the serialized block contract independently of cache plumbing."""

    schema = json.dumps(
        EvidenceBlock.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(schema).hexdigest()


def _semantic_value(value: object) -> str:
    """Normalize a parser enum without depending on the parser's Python types."""

    raw = getattr(value, "value", value)
    return re.sub(r"[-\s]+", "_", str(raw or "").strip().casefold())


def _block_id(source: str, page: int, order: int, kind: str, text: str) -> str:
    """Build a stable evidence ID from source location and content."""

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{source}_p{page}_{order:04d}_{kind}_{digest}"


def _normalize_layout_text(value: object) -> str:
    """Normalize layout whitespace while retaining line boundaries."""

    lines = [
        re.sub(r"[ \t]+", " ", line).strip() for line in str(value or "").splitlines()
    ]
    return "\n".join(line for line in lines if line)


def _formatted_line(line: dict[str, Any]) -> str:
    """Recover visually raised and lowered glyphs from native PDF geometry."""

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
    """Measure how much of the first rectangle is covered by the second."""

    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(1.0, first[2] - first[0]) * max(1.0, first[3] - first[1])
    return intersection / area


def _pymupdf_layout_items(path: Path) -> list[dict[str, Any]]:
    """Recover native text and tables without emitting table contents twice.

    Detected table rectangles suppress overlapping text blocks; all remaining
    layout_items retain geometry so later ordering and evidence locations stay stable.
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


def _classify_headings(layout_items: list[dict[str, Any]]) -> None:
    """Infer document hierarchy from typography rather than journal-specific labels."""

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
    """Turn backend layout_items into stable evidence with inherited section paths."""

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
    """Parse a born-digital PDF using the repository's required dependency."""

    return _evidence_blocks_from_layout(_pymupdf_layout_items(path), source)


def _docling_bbox(item: object) -> list[float] | None:
    """Read Docling provenance coordinates without depending on a specific release."""

    provenance = getattr(item, "prov", None) or []
    if not provenance:
        return None
    bbox = getattr(provenance[0], "bbox", None)
    values = [getattr(bbox, name, None) for name in ("l", "t", "r", "b")]
    return (
        [float(value) for value in values]
        if all(value is not None for value in values)
        else None
    )


def _native_typography_blocks(
    path: Path,
    source: str,
    start_order: int,
    included_pages: set[int] | None = None,
) -> list[EvidenceBlock]:
    """Supplement scientific pages with compact sub/superscript renderings."""

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


def _parse_docling(path: Path, source: str) -> list[EvidenceBlock]:
    """Adapt Docling structure without leaking its object model downstream.

    Native typography blocks are added only as compact supporting evidence for
    subscripts and superscripts that a layout conversion may normalize away.
    """

    try:
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import ContentLayer, DocItemLabel
    except ImportError as exc:
        raise RuntimeError("Docling is not installed; reinstall perla-extract") from exc

    document = DocumentConverter().convert(str(path)).document
    layout_items: list[dict[str, Any]] = []
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
    return blocks


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
    *, source_hash: str, source: str, parser: str, parser_version: str
) -> dict[str, object]:
    """Describe every input that can change the parsed block representation."""

    return {
        "cache_format_version": DOCUMENT_CACHE_FORMAT_VERSION,
        "parser_implementation_sha256": _parser_implementation_sha256(),
        "evidence_schema_sha256": _evidence_schema_sha256(),
        "source_sha256": source_hash,
        "source": source,
        "parser": parser,
        "parser_version": parser_version,
    }


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
    version = _package_version("docling" if parser == "docling" else "PyMuPDF")
    if version == "not-installed":
        raise RuntimeError(
            f"The selected {parser} parser is not installed; reinstall perla-extract"
        )
    identity = _cache_identity(
        source_hash=source_hash,
        source=source,
        parser=parser,
        parser_version=version,
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
        "parser_version": version,
        "parser_implementation_sha256": identity["parser_implementation_sha256"],
        "evidence_schema_sha256": identity["evidence_schema_sha256"],
        "cache_format_version": DOCUMENT_CACHE_FORMAT_VERSION,
        "cache_hit": cache_hit,
        "parsed_block_count": len(parsed_blocks),
        "excluded_block_count": excluded,
        "block_count": len(evidence_blocks),
    }


def _normalized_block_text_key(block: EvidenceBlock) -> str:
    """Normalize complete blocks only for cross-document duplicate detection."""

    text = unicodedata.normalize("NFKC", block.text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _deduplicate_evidence_blocks(
    blocks: list[EvidenceBlock],
) -> tuple[list[EvidenceBlock], int]:
    """Collapse a main-paper block repeated verbatim inside a concatenated SI."""

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
) -> tuple[list[EvidenceBlock], list[dict[str, object]]]:
    """Parse a main article and optional SI without dropping unique evidence."""

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
