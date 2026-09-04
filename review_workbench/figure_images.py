"""Locate and render main-text figures without changing extraction evidence."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.logging import logger
from perla_extract.study_extraction.source import _docling_runtime

FIGURE_PREFIX = re.compile(
    r"^\s*(?:fig(?:ure)?\.?)\s*(?P<number>[0-9]+)\b", re.IGNORECASE
)
FIGURE_IMAGE_FORMAT_VERSION = 2


class FigureRegion(BaseModel):
    """Identify one numbered figure in PDF top-left point coordinates."""

    model_config = ConfigDict(extra="forbid", strict=True)

    figure_number: str = Field(min_length=1, max_length=40)
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    caption: str = Field(min_length=1)
    caption_block_id: str | None = None
    localization_method: Literal["docling_picture"]

    @model_validator(mode="after")
    def require_positive_rectangle(self) -> "FigureRegion":
        if self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]:
            raise ValueError("figure region must have positive width and height")
        return self


class RenderedFigure(BaseModel):
    """Bind a deterministic crop to its PDF, caption, and layout coordinates."""

    model_config = ConfigDict(extra="forbid", strict=True)

    figure_number: str
    page: int
    bbox: list[float]
    caption: str
    caption_block_id: str | None
    localization_method: str
    image_path: str
    image_sha256: str
    width_pixels: int = Field(gt=0)
    height_pixels: int = Field(gt=0)


class FigureImageManifest(BaseModel):
    """Fingerprint every input that can change a visual model request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    format_version: int
    pdf_path: str
    pdf_sha256: str
    document_sha256: str | None
    docling_version: str
    dpi: int
    margin_points: float
    figures: list[RenderedFigure]
    captions_without_region: list[dict[str, object]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pdf_rect_from_docling_bbox(
    bbox: object, *, page_height: float
) -> list[float] | None:
    """Convert Docling provenance to PyMuPDF's top-left coordinate system."""

    values = [getattr(bbox, name, None) for name in ("l", "t", "r", "b")]
    if not all(isinstance(value, (int, float)) for value in values):
        return None
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    left, top, right, bottom = numeric
    origin = str(getattr(bbox, "coord_origin", "")).casefold()
    if "bottomleft" in origin:
        top, bottom = page_height - top, page_height - bottom
    x0, x1 = sorted((left, right))
    y0, y1 = sorted((top, bottom))
    return [x0, y0, x1, y1]


def _caption_index(document_path: Path | None) -> dict[tuple[int, str], dict[str, Any]]:
    """Index parser captions so visual proposals retain extraction block identity."""

    if document_path is None:
        return {}
    document = json.loads(document_path.read_text(encoding="utf-8"))
    result = {}
    for block in document.get("blocks", []):
        text = block.get("text")
        if block.get("source") != "main" or not isinstance(text, str):
            continue
        match = FIGURE_PREFIX.match(text)
        if match:
            result[(int(block["page"]), match.group("number"))] = block
    return result


def _resolved_caption(picture: object, document: object) -> str:
    """Join all caption fragments linked to one Docling picture item."""

    fragments = []
    for reference in getattr(picture, "captions", []) or []:
        try:
            resolved = reference.resolve(document)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        text = str(getattr(resolved, "text", "")).strip()
        if text:
            fragments.append(text)
    return " ".join(fragments)


def discover_figure_regions(
    pdf_path: Path, document_path: Path | None = None
) -> tuple[list[FigureRegion], list[dict[str, object]]]:
    """Associate Docling picture boxes with numbered main-paper captions.

    Only explicit Docling picture-to-caption links establish a region. The extraction
    document supplies stable caption block identifiers, but page proximity never creates
    a link: missing relations are reported for review instead of guessed.
    """

    converter, ContentLayer, _ = _docling_runtime()
    converted = converter.convert(str(pdf_path)).document
    captions = _caption_index(document_path)
    raw_regions: list[FigureRegion] = []
    with pymupdf.open(pdf_path) as pdf:
        items = converted.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
        )
        for item, _level in items:
            if "picture" not in type(item).__name__.casefold():
                continue
            provenance = getattr(item, "prov", None) or []
            if not provenance:
                continue
            page = int(getattr(provenance[0], "page_no", 0) or 0)
            if page < 1 or page > len(pdf):
                continue
            caption = _resolved_caption(item, converted)
            match = FIGURE_PREFIX.match(caption)
            if not match:
                continue
            number = match.group("number")
            caption_block = captions.get((page, number))
            bbox = pdf_rect_from_docling_bbox(
                getattr(provenance[0], "bbox", None),
                page_height=float(pdf[page - 1].rect.height),
            )
            if not bbox or not number:
                continue
            raw_regions.append(
                FigureRegion(
                    figure_number=number,
                    page=page,
                    bbox=bbox,
                    caption=caption,
                    caption_block_id=(
                        str(caption_block["block_id"]) if caption_block else None
                    ),
                    localization_method="docling_picture",
                )
            )

    grouped: dict[tuple[int, str], FigureRegion] = {}
    for region in raw_regions:
        key = (region.page, region.figure_number)
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = region
            continue
        previous.bbox = [
            min(previous.bbox[0], region.bbox[0]),
            min(previous.bbox[1], region.bbox[1]),
            max(previous.bbox[2], region.bbox[2]),
            max(previous.bbox[3], region.bbox[3]),
        ]
        if len(region.caption) > len(previous.caption):
            previous.caption = region.caption

    found = set(grouped)
    missing = [
        {
            "figure_number": number,
            "page": page,
            "caption_block_id": block.get("block_id"),
            "caption": block.get("text"),
        }
        for (page, number), block in captions.items()
        if (page, number) not in found
    ]
    return sorted(
        grouped.values(), key=lambda item: (item.page, item.figure_number)
    ), missing


def render_figure_regions(
    pdf_path: Path,
    regions: list[FigureRegion],
    output_dir: Path,
    *,
    dpi: int = 180,
    margin_points: float = 6,
) -> list[RenderedFigure]:
    """Render stable PNG crops and retain the exact source rectangle for auditing."""

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    scale = dpi / 72
    with pymupdf.open(pdf_path) as pdf:
        for region in regions:
            page = pdf[region.page - 1]
            crop = (
                pymupdf.Rect(
                    region.bbox[0] - margin_points,
                    region.bbox[1] - margin_points,
                    region.bbox[2] + margin_points,
                    region.bbox[3] + margin_points,
                )
                & page.rect
            )
            filename = (
                f"figure-{int(region.figure_number):03d}-page-{region.page:03d}.png"
            )
            path = output_dir / filename
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale), clip=crop, alpha=False
            )
            pixmap.save(path)
            payload = region.model_dump(mode="json")
            payload["bbox"] = [crop.x0, crop.y0, crop.x1, crop.y1]
            rendered.append(
                RenderedFigure(
                    **payload,
                    image_path=str(path),
                    image_sha256=_sha256(path),
                    width_pixels=pixmap.width,
                    height_pixels=pixmap.height,
                )
            )
    return rendered


def build_figure_image_manifest(
    pdf_path: Path,
    output_dir: Path,
    *,
    document_path: Path | None = None,
    dpi: int = 180,
    margin_points: float = 6,
    refresh: bool = False,
) -> FigureImageManifest:
    """Return a cache-validated manifest or perform local layout and rendering."""

    manifest_path = output_dir / "figure-images.json"
    pdf_hash = _sha256(pdf_path)
    document_hash = _sha256(document_path) if document_path else None
    docling_version = importlib.metadata.version("docling")
    if manifest_path.exists() and not refresh:
        try:
            cached = FigureImageManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if (
                cached.format_version == FIGURE_IMAGE_FORMAT_VERSION
                and cached.pdf_sha256 == pdf_hash
                and cached.document_sha256 == document_hash
                and cached.docling_version == docling_version
                and cached.dpi == dpi
                and cached.margin_points == margin_points
                and all(
                    Path(item.image_path).exists()
                    and _sha256(Path(item.image_path)) == item.image_sha256
                    for item in cached.figures
                )
            ):
                logger.info("Using cached figure images for {}", pdf_path.name)
                return cached
        except (OSError, ValueError):
            logger.warning("Ignoring invalid figure-image cache for {}", pdf_path.name)

    regions, missing = discover_figure_regions(pdf_path, document_path)
    figures = render_figure_regions(
        pdf_path,
        regions,
        output_dir / "figures",
        dpi=dpi,
        margin_points=margin_points,
    )
    manifest = FigureImageManifest(
        format_version=FIGURE_IMAGE_FORMAT_VERSION,
        pdf_path=str(pdf_path),
        pdf_sha256=pdf_hash,
        document_sha256=document_hash,
        docling_version=docling_version,
        dpi=dpi,
        margin_points=margin_points,
        figures=figures,
        captions_without_region=missing,
    )
    write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
    return manifest


__all__ = [
    "FigureImageManifest",
    "FigureRegion",
    "RenderedFigure",
    "build_figure_image_manifest",
    "discover_figure_regions",
    "pdf_rect_from_docling_bbox",
    "render_figure_regions",
]
