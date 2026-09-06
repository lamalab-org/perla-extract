"""Classify rendered figures and prepare conservative review-app proposals."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import unicodedata
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import click
from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.logging import configure_logging, logger
from review_workbench.figure_images import (
    FigureImageManifest,
    RenderedFigure,
    build_figure_image_manifest,
)
from review_workbench.study_review import FigureClass

VISION_PROMPT_VERSION = 1

if TYPE_CHECKING:
    from perla_extract.study_extraction.client import ModelClient


class VisibleAtomicValue(BaseModel):
    """Record one explicitly printed schema candidate, never a sampled plot point."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=300)
    raw_value: str = Field(min_length=1, max_length=500)
    context: str = Field(min_length=1, max_length=500)
    schema_target: Literal[
        "performance_observation",
        "population_statistic",
        "stability_test",
        "device_structure",
        "processing_condition",
        "not_in_schema",
    ]
    presentation: Literal["printed_label", "inset_table"]


class VisualPanelProposal(BaseModel):
    """Describe one visually distinct panel inside a rendered main figure."""

    model_config = ConfigDict(extra="forbid", strict=True)

    panel_label: str = Field(default="", max_length=20)
    panel_bbox_normalized: list[int] | None = Field(
        default=None, min_length=4, max_length=4
    )
    figure_class: FigureClass
    description: str = Field(min_length=1, max_length=1000)
    x_axis_label: str | None = Field(default=None, max_length=300)
    y_axis_label: str | None = Field(default=None, max_length=300)
    data_presentation: Literal[
        "no_numeric_data",
        "explicit_numeric_labels",
        "inset_table",
        "plotted_values_only",
        "mixed",
        "uncertain",
    ]
    extraction_feasibility: Literal[
        "straightforward",
        "partly_straightforward",
        "requires_digitization",
        "not_applicable",
        "uncertain",
    ]
    schema_relevant: bool
    explicit_values: list[VisibleAtomicValue] = Field(default_factory=list)
    visual_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_panel_geometry_and_effort(self) -> "VisualPanelProposal":
        if self.panel_bbox_normalized is not None:
            x0, y0, x1, y1 = self.panel_bbox_normalized
            if not all(0 <= value <= 1000 for value in (x0, y0, x1, y1)):
                raise ValueError(
                    "normalized panel coordinates must be between 0 and 1000"
                )
            if x1 <= x0 or y1 <= y0:
                raise ValueError("normalized panel rectangle must have positive size")
        expected = {
            "no_numeric_data": "not_applicable",
            "explicit_numeric_labels": "straightforward",
            "inset_table": "straightforward",
            "plotted_values_only": "requires_digitization",
            "mixed": "partly_straightforward",
            "uncertain": "uncertain",
        }[self.data_presentation]
        if self.extraction_feasibility != expected:
            raise ValueError("extraction feasibility must follow data presentation")
        if self.explicit_values and self.data_presentation not in {
            "explicit_numeric_labels",
            "inset_table",
            "mixed",
        }:
            raise ValueError(
                "explicit values require printed labels, a table, or mixed data"
            )
        return self


class VisualFigureProposal(BaseModel):
    """Bind panel classifications to the exact rendered figure bytes."""

    model_config = ConfigDict(extra="forbid", strict=True)

    figure_number: str
    image_sha256: str = Field(min_length=64, max_length=64)
    panels: list[VisualPanelProposal] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_panel_labels(self) -> "VisualFigureProposal":
        labels = [panel.panel_label.casefold() for panel in self.panels]
        if len(labels) != len(set(labels)):
            raise ValueError("panel labels must be unique within a figure")
        return self


class VisualPaperProposal(BaseModel):
    """Return all supplied figures for one paper in a single model response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_id: str
    figures: list[VisualFigureProposal]

    @model_validator(mode="after")
    def require_unique_figures(self) -> "VisualPaperProposal":
        identities = [item.figure_number.casefold() for item in self.figures]
        if len(identities) != len(set(identities)):
            raise ValueError("figure numbers must be unique within a paper")
        return self


def _image_data_url(path: Path) -> str:
    """Encode one local crop for LiteLLM's provider-neutral image input."""

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _vision_prompt_content(
    paper_id: str, figures: list[RenderedFigure]
) -> list[dict[str, object]]:
    """Interleave figure provenance and pixels so images cannot be mis-associated."""

    instructions = """Inspect every supplied main-text figure and return every visually
distinct labeled panel. Copy paper_id, figure_number, and image_sha256 exactly. Panel
coordinates use [x0,y0,x1,y1] on a 0-1000 top-left grid; use null if boundaries are
uncertain.

Choose one primary class: jv, eqe, population_statistics, stability,
characterization, device_structure, or other. EQE includes integrated EQE. Stability
means device performance over time. Device structure means a layer-stack schematic or
annotated structural microscopy; ordinary microscopy is characterization.

Transcribe axis labels only when legible. Numeric presentation describes scientific
data, not axis ticks: no_numeric_data, explicit_numeric_labels, inset_table,
plotted_values_only, mixed, or uncertain. Extraction feasibility follows directly:
not_applicable, straightforward, straightforward, requires_digitization,
partly_straightforward, or uncertain respectively.

StudyExtraction covers device composition/layers, processing, individual performance,
population statistics, and stability. Mark schema_relevant accordingly. explicit_values
may contain only atomic values printed as annotations or in inset tables that map to the
schema. Never include axis ticks, legend labels, curve samples, visually estimated
coordinates, or values found only in the caption. Preserve each printed value verbatim
and keep different metrics in separate objects. Use visual_notes for ambiguity."""
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": f"{instructions}\n\npaper_id: {paper_id}",
        }
    ]
    for figure in figures:
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"figure_number: {figure.figure_number}\n"
                        f"image_sha256: {figure.image_sha256}\n"
                        f"caption: {figure.caption}"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": _image_data_url(Path(figure.image_path)),
                        "detail": "high",
                    },
                },
            ]
        )
    return content


def _validate_visual_response(
    result: VisualPaperProposal,
    paper_id: str,
    figures: list[RenderedFigure],
) -> None:
    """Reject image swaps, omissions, or invented figure identities."""

    if result.paper_id != paper_id:
        raise ValueError("vision response changed paper_id")
    expected = {(item.figure_number, item.image_sha256) for item in figures}
    returned = {(item.figure_number, item.image_sha256) for item in result.figures}
    if returned != expected:
        raise ValueError("vision response omitted, invented, or swapped a figure")


def validate_saved_figure_proposal(
    artifact: dict[str, Any],
    *,
    paper_id: str,
    manifest: FigureImageManifest,
    model: str,
) -> dict[str, Any]:
    """Return a reusable proposal only when its sources and model output still match.

    Long visual batches are intentionally resumable. Reuse must therefore verify the
    source fingerprints and re-run both structural and figure-identity validation;
    merely finding a JSON file is not enough evidence that it is safe to publish.
    """

    if (
        artifact.get("format_version") != 1
        or artifact.get("vision_prompt_version") != VISION_PROMPT_VERSION
        or artifact.get("paper_id") != paper_id
        or artifact.get("model") != model
        or artifact.get("pdf_sha256") != manifest.pdf_sha256
        or artifact.get("document_sha256") != manifest.document_sha256
    ):
        raise ValueError("saved proposal does not match the current inputs")
    result = VisualPaperProposal.model_validate(
        {"paper_id": paper_id, "figures": artifact.get("figures")}
    )
    _validate_visual_response(result, paper_id, manifest.figures)
    proposal = artifact.get("review_proposal")
    if not isinstance(proposal, dict) or not isinstance(proposal.get("panels"), list):
        raise ValueError("saved proposal has no editable panel rows")
    return proposal


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _text_matches(value: VisibleAtomicValue, document: dict[str, object]) -> list[str]:
    """Find exact value-plus-name support without declaring semantic absence."""

    raw = _normalized(value.raw_value)
    name_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", _normalized(value.name))
        if len(token) > 2
    }
    matches: list[str] = []
    blocks = document.get("blocks")
    if not isinstance(blocks, list):
        return matches
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        text = _normalized(block["text"])
        if raw in text and (
            not name_tokens or name_tokens & set(re.findall(r"[a-z0-9]+", text))
        ):
            matches.append(str(block.get("block_id", "")))
    return [block_id for block_id in matches if block_id]


def build_review_proposal(
    result: VisualPaperProposal,
    manifest: FigureImageManifest,
    document: dict[str, object],
) -> dict[str, Any]:
    """Convert vision output into editable rows while keeping loss counts human-owned."""

    by_figure = {item.figure_number: item for item in manifest.figures}
    panels = []
    for figure in result.figures:
        source = by_figure[figure.figure_number]
        for panel_index, panel in enumerate(figure.panels):
            identity = json.dumps(
                {
                    "image_sha256": figure.image_sha256,
                    "figure_number": figure.figure_number,
                    "panel_label": panel.panel_label,
                    "panel_bbox_normalized": panel.panel_bbox_normalized,
                    "panel_index": panel_index,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            candidates = []
            for value in panel.explicit_values:
                matches = _text_matches(value, document)
                candidates.append(
                    {
                        **value.model_dump(mode="json"),
                        "text_comparison": (
                            "exact_text_match" if matches else "needs_human_comparison"
                        ),
                        "matching_block_ids": matches,
                    }
                )
            panels.append(
                {
                    "proposal_panel_id": hashlib.sha256(identity).hexdigest()[:24],
                    "figure_number": figure.figure_number,
                    "panel_label": panel.panel_label,
                    "page": source.page,
                    "caption_block_id": source.caption_block_id,
                    "figure_class": panel.figure_class,
                    "description": panel.description,
                    "x_axis_label": panel.x_axis_label,
                    "y_axis_label": panel.y_axis_label,
                    "data_presentation": panel.data_presentation,
                    "extraction_feasibility": panel.extraction_feasibility,
                    "schema_relevant": panel.schema_relevant,
                    "figure_only_records": 0,
                    "figure_only_atomic_values": 0,
                    "panel_bbox_normalized": panel.panel_bbox_normalized,
                    "figure_bbox_pdf": source.bbox,
                    "visual_candidates": candidates,
                    "visual_notes": panel.visual_notes,
                    "image_sha256": figure.image_sha256,
                }
            )
    return {"proposal_method": "caption_and_figure", "panels": panels}


def attach_review_geometry(
    proposal: dict[str, Any], manifest: FigureImageManifest
) -> dict[str, Any]:
    """Attach deterministic crop identity and coordinates to a cached proposal.

    Model responses intentionally contain coordinates relative to the rendered figure.
    The app also needs the figure's PDF rectangle to display the selected panel without
    shipping generated crop files. This enrichment is local and does not change the
    scientific classification.
    """

    by_figure = {item.figure_number: item for item in manifest.figures}
    for index, panel in enumerate(proposal.get("panels", [])):
        source = by_figure.get(str(panel.get("figure_number", "")))
        if source is None:
            continue
        panel["figure_bbox_pdf"] = source.bbox
        identity = json.dumps(
            {
                "image_sha256": source.image_sha256,
                "figure_number": panel.get("figure_number"),
                "panel_label": panel.get("panel_label"),
                "panel_bbox_normalized": panel.get("panel_bbox_normalized"),
                "index": index,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        panel.setdefault(
            "proposal_panel_id", hashlib.sha256(identity).hexdigest()[:24]
        )
    return proposal


def classify_figure_images(
    *,
    paper_id: str,
    manifest: FigureImageManifest,
    document_path: Path,
    output_path: Path,
    model: str,
    cache_dir: Path,
    reasoning_effort: str | None,
    max_cost_usd: float,
    client: ModelClient | None = None,
    max_figures_per_call: int = 6,
) -> dict[str, Any]:
    """Run bounded cached visual calls and write a source-fingerprinted proposal."""

    if not manifest.figures:
        raise click.ClickException(
            "No localized figures are available for classification"
        )
    if client is None:
        from perla_extract.study_extraction.client import ModelClient

        client = ModelClient(
            cache_dir=cache_dir,
            output_dir=output_path.parent,
            temperature=0,
            max_model_calls=2 * math.ceil(len(manifest.figures) / max_figures_per_call),
            max_cost_usd=max_cost_usd,
        )
    call_start = len(client.calls)
    figure_results = []
    for start in range(0, len(manifest.figures), max_figures_per_call):
        chunk = manifest.figures[start : start + max_figures_per_call]
        suffix = (
            ""
            if len(manifest.figures) <= max_figures_per_call
            else f"-figures-{start + 1}-{start + len(chunk)}"
        )
        chunk_result = client.complete(
            kind="figure_vision",
            slug=f"{paper_id}{suffix}",
            model=model,
            system=(
                "You inspect scientific figures conservatively. Never infer values "
                "from plot coordinates and never claim text that is not visibly "
                "legible."
            ),
            prompt=_vision_prompt_content(paper_id, chunk),
            response_model=VisualPaperProposal,
            max_output_tokens=30000,
            reasoning_effort=reasoning_effort,
            validate=partial(
                _validate_visual_response, paper_id=paper_id, figures=chunk
            ),
            validation_contract=f"figure-vision-{VISION_PROMPT_VERSION}",
        )
        figure_results.extend(chunk_result.figures)
    result = VisualPaperProposal(paper_id=paper_id, figures=figure_results)
    _validate_visual_response(result, paper_id, manifest.figures)
    document = json.loads(document_path.read_text(encoding="utf-8"))
    artifact = {
        "format_version": 1,
        "method": "caption_and_figure_model_proposal",
        "vision_prompt_version": VISION_PROMPT_VERSION,
        "paper_id": paper_id,
        "model": model,
        "pdf_sha256": manifest.pdf_sha256,
        "document_sha256": manifest.document_sha256,
        "rendering": {"dpi": manifest.dpi, "margin_points": manifest.margin_points},
        "figures": [item.model_dump(mode="json") for item in result.figures],
        "review_proposal": build_review_proposal(result, manifest, document),
        "captions_without_region": manifest.captions_without_region,
        "model_calls": client.calls[call_start:],
        "budget": client.budget_status(),
    }
    write_json_atomic(output_path, artifact)
    return artifact


@click.command()
@click.option("--paper-id", required=True)
@click.option(
    "--pdf", "pdf_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option(
    "--document",
    "document_path",
    type=click.Path(path_type=Path, exists=True),
    required=True,
)
@click.option("--output-dir", type=click.Path(path_type=Path), required=True)
@click.option(
    "--model", help="LiteLLM model name. Required unless --render-only is used."
)
@click.option("--reasoning-effort", default=None)
@click.option("--dpi", type=click.IntRange(min=96, max=300), default=180)
@click.option("--max-figures-per-call", type=click.IntRange(min=1, max=12), default=6)
@click.option(
    "--max-cost-usd", type=click.FloatRange(min=0, min_open=True), default=2.0
)
@click.option(
    "--model-cache-dir", type=click.Path(path_type=Path), default=".perla-cache/models"
)
@click.option("--refresh-figures", is_flag=True)
@click.option("--render-only", is_flag=True)
@click.option("--log-level", default="INFO")
def main(
    paper_id: str,
    pdf_path: Path,
    document_path: Path,
    output_dir: Path,
    model: str | None,
    reasoning_effort: str | None,
    dpi: int,
    max_figures_per_call: int,
    max_cost_usd: float,
    model_cache_dir: Path,
    refresh_figures: bool,
    render_only: bool,
    log_level: str,
) -> None:
    """Render figures locally, then optionally create visual review proposals."""

    configure_logging(level=log_level)
    manifest = build_figure_image_manifest(
        pdf_path,
        output_dir,
        document_path=document_path,
        dpi=dpi,
        refresh=refresh_figures,
    )
    logger.info(
        "Localized {} figures; {} captions need manual localization",
        len(manifest.figures),
        len(manifest.captions_without_region),
    )
    if render_only:
        click.echo(output_dir / "figure-images.json")
        return
    if not model:
        raise click.UsageError("--model is required unless --render-only is used")
    artifact = classify_figure_images(
        paper_id=paper_id,
        manifest=manifest,
        document_path=document_path,
        output_path=output_dir / "figure-vision-proposal.json",
        model=model,
        cache_dir=model_cache_dir,
        reasoning_effort=reasoning_effort,
        max_cost_usd=max_cost_usd,
        max_figures_per_call=max_figures_per_call,
    )
    click.echo(
        f"Wrote {len(artifact['review_proposal']['panels'])} panel proposals to "
        f"{output_dir / 'figure-vision-proposal.json'}"
    )


if __name__ == "__main__":
    main()
