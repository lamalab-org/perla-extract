"""Create reviewable subfigure proposals from main-text figure captions."""

from __future__ import annotations

import hashlib
import json
import re
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypedDict

import click
from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.client import ModelClient
from perla_extract.study_extraction.logging import logger
from review_workbench.study_review import FigureClass

CAPTION_PATTERN = re.compile(
    r"^\s*(?:fig(?:ure)?\.?)\s*(?P<number>[0-9]+)\b", re.IGNORECASE
)
PROMPT_VERSION = 1


class CaptionInput(TypedDict):
    caption_block_id: str
    figure_number: str
    page: int | None
    caption: str


class PaperInput(TypedDict):
    paper_id: str
    captions: list[CaptionInput]


class CaptionPanelProposal(BaseModel):
    """Describe one panel using only information supported by its caption."""

    model_config = ConfigDict(extra="forbid", strict=True)

    caption_block_id: str = Field(min_length=1)
    figure_number: str = Field(min_length=1, max_length=40)
    panel_label: str = Field(default="", max_length=20)
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

    @model_validator(mode="after")
    def keep_effort_consistent_with_presentation(self) -> "CaptionPanelProposal":
        """Make the recoverability label a consequence, not a second model opinion."""

        expected = {
            "no_numeric_data": "not_applicable",
            "explicit_numeric_labels": "straightforward",
            "inset_table": "straightforward",
            "plotted_values_only": "requires_digitization",
            "mixed": "partly_straightforward",
            "uncertain": "uncertain",
        }[self.data_presentation]
        if self.extraction_feasibility != expected:
            raise ValueError(
                "extraction feasibility must follow the numeric presentation"
            )
        return self


class PaperFigureProposal(BaseModel):
    """Keep proposed panels attached to the paper that supplied their captions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_id: str = Field(min_length=1)
    panels: list[CaptionPanelProposal]

    @model_validator(mode="after")
    def require_unique_panels(self) -> "PaperFigureProposal":
        keys = [
            (panel.figure_number.casefold(), panel.panel_label.casefold())
            for panel in self.panels
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("figure number and panel label must be unique per paper")
        return self


class FigureProposalBatch(BaseModel):
    """Return several papers in one call to avoid per-paper request overhead."""

    model_config = ConfigDict(extra="forbid", strict=True)

    papers: list[PaperFigureProposal]


def caption_blocks(document_path: Path) -> list[CaptionInput]:
    """Select main-text captions while excluding references and SI figures.

    Caption recognition uses only the conventional line prefix and a numeric main
    figure identifier. Scientific keywords play no role, so adding a new figure class
    does not require changing document parsing.
    """

    document = json.loads(document_path.read_text(encoding="utf-8"))
    captions: list[CaptionInput] = []
    for block in document.get("blocks", []):
        text = block.get("text")
        if block.get("source") != "main" or not isinstance(text, str):
            continue
        metadata = block.get("metadata")
        if isinstance(metadata, dict) and metadata.get("docling_label") not in {
            None,
            "caption",
        }:
            continue
        match = CAPTION_PATTERN.match(text)
        if not match:
            continue
        page = block.get("page")
        captions.append(
            {
                "caption_block_id": str(block["block_id"]),
                "figure_number": match.group("number"),
                "page": page if isinstance(page, int) else None,
                "caption": text,
            }
        )
    return captions


def _prompt(batch: list[PaperInput]) -> str:
    """Give the classifier the schema boundary and explicit non-invention rules."""

    return f"""Classify every main-text figure caption below into reviewable panels.

Return every input paper exactly once and every caption at least once. For explicitly
named panels or ranges, return one row per panel (A to C means A, B, and C). Use an
empty panel_label only when the caption does not distinguish panels. Copy paper_id,
caption_block_id, and figure_number exactly.

Choose one primary scientific class:
- jv: current-density/voltage curves
- eqe: EQE spectra or integrated EQE
- population_statistics: distributions, box/violin/scatter population comparisons
- stability: device performance over time
- characterization: spectroscopy, diffraction, microscopy, morphology, and other
  material/device characterization
- device_structure: layer-stack schematics or annotated structural microscopy
- other: process diagrams, mechanisms, calculations, or anything above does not cover

StudyExtraction represents photovoltaic device composition and layers, processing,
individual performance observations, population statistics, and stability tests.
schema_relevant means the panel can contribute one of those facts; characterization
alone is normally outside the schema.

Paraphrase a concise description. Never infer an axis label: set it to null unless the
caption states the label explicitly. Classify numeric presentation only when the
caption supports it. Curves/spectra/plots imply plotted_values_only; an explicitly
stated inset table implies inset_table; explicitly stated printed numeric labels imply
explicit_numeric_labels; use mixed only when both are stated; otherwise use uncertain
or no_numeric_data. Match extraction_feasibility accordingly: printed labels or tables
are straightforward, plotted values require digitization, mixed is partly
straightforward, no numeric data is not_applicable, and uncertain remains uncertain.

Inputs:
{json.dumps(batch, ensure_ascii=False, separators=(",", ":"))}
"""


def _validate_batch(result: FigureProposalBatch, batch: list[PaperInput]) -> None:
    """Reject omitted or invented captions before proposals reach reviewers."""

    expected_papers = {str(item["paper_id"]): item for item in batch}
    returned_papers = {paper.paper_id: paper for paper in result.papers}
    if returned_papers.keys() != expected_papers.keys():
        raise ValueError("classifier did not return exactly the requested papers")
    for paper_id, paper in returned_papers.items():
        captions = {
            str(item["caption_block_id"]): str(item["figure_number"])
            for item in expected_papers[paper_id]["captions"]
        }
        returned_blocks = {panel.caption_block_id for panel in paper.panels}
        if returned_blocks != captions.keys():
            raise ValueError(f"classifier omitted or invented captions for {paper_id}")
        if any(
            panel.figure_number != captions[panel.caption_block_id]
            for panel in paper.panels
        ):
            raise ValueError(f"classifier changed a figure number for {paper_id}")


def classify_captions(
    *,
    documents_dir: Path,
    output: Path,
    model: str,
    batch_size: int,
    cache_dir: Path,
    reasoning_effort: str | None,
    max_model_calls: int,
    max_cost_usd: float,
) -> dict[str, Any]:
    """Classify caption batches and write a static, review-only proposal artifact."""

    papers: list[PaperInput] = []
    for document_path in sorted(documents_dir.glob("*/document.json")):
        captions = caption_blocks(document_path)
        if captions:
            papers.append({"paper_id": document_path.parent.name, "captions": captions})
    if not papers:
        raise click.ClickException(
            f"No main-text figure captions found in {documents_dir}"
        )

    run_dir = output.parent / ".figure-census-requests"
    client = ModelClient(
        cache_dir=cache_dir,
        output_dir=run_dir,
        temperature=0,
        max_model_calls=max_model_calls,
        max_cost_usd=max_cost_usd,
    )
    proposals: dict[str, object] = {}
    for start in range(0, len(papers), batch_size):
        batch = papers[start : start + batch_size]
        logger.info(
            "Classifying figure captions for papers {}-{} of {}",
            start + 1,
            start + len(batch),
            len(papers),
        )
        result = client.complete(
            kind="figure_census",
            slug=f"batch-{start // batch_size + 1}",
            model=model,
            system=(
                "You classify scientific figure captions conservatively. Use only the "
                "provided caption text and never claim visual inspection."
            ),
            prompt=_prompt(batch),
            response_model=FigureProposalBatch,
            max_output_tokens=30000,
            reasoning_effort=reasoning_effort,
            validate=partial(_validate_batch, batch=batch),
            validation_contract=f"figure-caption-{PROMPT_VERSION}",
        )
        caption_pages = {
            str(item["caption_block_id"]): item.get("page")
            for paper in batch
            for item in paper["captions"]
        }
        for paper in result.papers:
            proposals[paper.paper_id] = {
                "panels": [
                    {
                        "proposal_panel_id": hashlib.sha256(
                            json.dumps(
                                {
                                    "paper_id": paper.paper_id,
                                    "caption_block_id": panel.caption_block_id,
                                    "figure_number": panel.figure_number,
                                    "panel_label": panel.panel_label,
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()[:24],
                        **panel.model_dump(mode="json"),
                        "page": caption_pages[panel.caption_block_id],
                        "figure_only_records": 0,
                        "figure_only_atomic_values": 0,
                    }
                    for panel in paper.panels
                ]
            }

    usage_records = [
        usage for call in client.calls if isinstance((usage := call.get("usage")), dict)
    ]
    artifact: dict[str, Any] = {
        "format_version": 1,
        "method": "caption_model_proposal",
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "limitations": (
            "Caption-only proposal. Review the rendered panel; axes, inset content, "
            "numeric presentation, schema relevance, and figure-only counts may change."
        ),
        "papers": proposals,
        "usage": {
            "live_calls": sum(not bool(call.get("cache_hit")) for call in client.calls),
            "cache_hits": sum(bool(call.get("cache_hit")) for call in client.calls),
            "prompt_tokens": sum(
                int(item.get("prompt_tokens", 0) or 0) for item in usage_records
            ),
            "completion_tokens": sum(
                int(item.get("completion_tokens", 0) or 0) for item in usage_records
            ),
            "total_tokens": sum(
                int(item.get("total_tokens", 0) or 0) for item in usage_records
            ),
            "cost": round(
                sum(float(item.get("cost", 0) or 0) for item in usage_records), 8
            ),
        },
    }
    write_json_atomic(output, artifact)
    return artifact


@click.command()
@click.option(
    "--documents-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option(
    "--model", default="openrouter/openai/gpt-5.6-sol:exacto", show_default=True
)
@click.option("--batch-size", type=click.IntRange(min=1, max=20), default=6)
@click.option(
    "--cache-dir", type=click.Path(path_type=Path), default=".perla-cache/models"
)
@click.option("--reasoning-effort", default=None)
@click.option("--max-model-calls", type=click.IntRange(min=1), default=12)
@click.option(
    "--max-cost-usd", type=click.FloatRange(min=0, min_open=True), default=2.0
)
def main(
    documents_dir: Path,
    output: Path,
    model: str,
    batch_size: int,
    cache_dir: Path,
    reasoning_effort: str | None,
    max_model_calls: int,
    max_cost_usd: float,
) -> None:
    """Populate caption-grounded figure-panel proposals for the review app."""

    artifact = classify_captions(
        documents_dir=documents_dir,
        output=output,
        model=model,
        batch_size=batch_size,
        cache_dir=cache_dir,
        reasoning_effort=reasoning_effort,
        max_model_calls=max_model_calls,
        max_cost_usd=max_cost_usd,
    )
    click.echo(f"Wrote proposals for {len(artifact['papers'])} papers to {output}")


if __name__ == "__main__":
    main()
