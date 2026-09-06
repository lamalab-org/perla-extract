"""Render or classify main-text figures for a directory of extraction runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.logging import configure_logging, logger
from review_workbench.figure_images import build_figure_image_manifest
from review_workbench.figure_vision import (
    attach_review_geometry,
    classify_figure_images,
    validate_saved_figure_proposal,
)


def _run_inputs(run_dir: Path) -> tuple[Path, Path]:
    """Resolve the main PDF from the extraction's frozen configuration."""

    configuration_path = run_dir / "run_configuration.json"
    document_path = run_dir / "document.json"
    if not configuration_path.exists() or not document_path.exists():
        raise ValueError("run must contain run_configuration.json and document.json")
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    pdf = configuration.get("pdf")
    if not isinstance(pdf, str) or not Path(pdf).is_file():
        raise ValueError("configured main PDF is not available on this computer")
    return Path(pdf), document_path


@click.command()
@click.option(
    "--runs-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--output-dir", type=click.Path(path_type=Path), required=True)
@click.option("--proposal-output", type=click.Path(path_type=Path), required=True)
@click.option("--model", help="LiteLLM model name; omit only with --render-only.")
@click.option("--reasoning-effort", default=None)
@click.option("--render-only", is_flag=True)
@click.option("--dpi", type=click.IntRange(min=96, max=300), default=180)
@click.option("--max-figures-per-call", type=click.IntRange(min=1, max=12), default=6)
@click.option("--max-model-calls", type=click.IntRange(min=1), default=40)
@click.option(
    "--max-cost-usd", type=click.FloatRange(min=0, min_open=True), default=20.0
)
@click.option(
    "--model-cache-dir", type=click.Path(path_type=Path), default=".perla-cache/models"
)
@click.option("--refresh-figures", is_flag=True)
@click.option("--exclude-paper", multiple=True)
@click.option("--log-level", default="INFO")
def main(
    runs_dir: Path,
    output_dir: Path,
    proposal_output: Path,
    model: str | None,
    reasoning_effort: str | None,
    render_only: bool,
    dpi: int,
    max_figures_per_call: int,
    max_model_calls: int,
    max_cost_usd: float,
    model_cache_dir: Path,
    refresh_figures: bool,
    exclude_paper: tuple[str, ...],
    log_level: str,
) -> None:
    """Process each paper independently and checkpoint successes and failures."""

    configure_logging(level=log_level)
    if not render_only and not model:
        raise click.UsageError("--model is required unless --render-only is used")
    client = None
    budget_error_types: tuple[type[BaseException], ...] = ()
    if not render_only:
        from perla_extract.study_extraction.client import (
            ModelBudgetExceeded,
            ModelClient,
        )

        budget_error_types = (ModelBudgetExceeded,)
        client = ModelClient(
            cache_dir=model_cache_dir,
            output_dir=output_dir,
            temperature=0,
            max_model_calls=max_model_calls,
            max_cost_usd=max_cost_usd,
        )
    artifact: dict[str, Any] = {
        "format_version": 1,
        "method": (
            "local_figure_rendering"
            if render_only
            else "caption_and_figure_model_proposal"
        ),
        "model": model,
        "papers": {},
        "failures": {},
        "skipped": sorted(exclude_paper),
    }
    run_dirs = sorted(
        path.parent
        for path in runs_dir.glob("*/document.json")
        if path.parent.name not in exclude_paper
    )
    if not run_dirs:
        raise click.ClickException(f"No extraction runs found in {runs_dir}")
    for index, run_dir in enumerate(run_dirs, start=1):
        paper_id = run_dir.name
        logger.info("Figure census {} of {}: {}", index, len(run_dirs), paper_id)
        try:
            pdf_path, document_path = _run_inputs(run_dir)
            paper_output = output_dir / paper_id
            manifest = build_figure_image_manifest(
                pdf_path,
                paper_output,
                document_path=document_path,
                dpi=dpi,
                refresh=refresh_figures,
            )
            if render_only:
                artifact["papers"][paper_id] = {
                    "localized_figures": len(manifest.figures),
                    "captions_without_region": manifest.captions_without_region,
                }
            else:
                proposal_path = paper_output / "figure-vision-proposal.json"
                if proposal_path.exists():
                    existing = json.loads(proposal_path.read_text(encoding="utf-8"))
                    try:
                        proposal = validate_saved_figure_proposal(
                            existing,
                            paper_id=paper_id,
                            manifest=manifest,
                            model=str(model),
                        )
                    except (TypeError, ValueError):
                        logger.warning(
                            "Ignoring stale or invalid figure proposal for {}", paper_id
                        )
                    else:
                        logger.info("Using validated figure proposal for {}", paper_id)
                        attach_review_geometry(proposal, manifest)
                        proposal["captions_without_region"] = (
                            manifest.captions_without_region
                        )
                        proposal["pdf_sha256"] = manifest.pdf_sha256
                        proposal["document_sha256"] = manifest.document_sha256
                        artifact["papers"][paper_id] = proposal
                        write_json_atomic(proposal_output, artifact)
                        continue
                result = classify_figure_images(
                    paper_id=paper_id,
                    manifest=manifest,
                    document_path=document_path,
                    output_path=proposal_path,
                    model=str(model),
                    cache_dir=model_cache_dir,
                    reasoning_effort=reasoning_effort,
                    max_cost_usd=max_cost_usd,
                    client=client,
                    max_figures_per_call=max_figures_per_call,
                )
                proposal = result["review_proposal"]
                proposal["captions_without_region"] = manifest.captions_without_region
                proposal["pdf_sha256"] = manifest.pdf_sha256
                proposal["document_sha256"] = manifest.document_sha256
                artifact["papers"][paper_id] = proposal
        except Exception as exc:  # keep completed papers recoverable in a long batch
            logger.error("Figure census failed for {}: {}", paper_id, exc)
            artifact["failures"][paper_id] = str(exc)
            if isinstance(exc, budget_error_types):
                artifact["status"] = "stopped_budget_unverifiable"
                write_json_atomic(proposal_output, artifact)
                break
        if client is not None:
            artifact["budget"] = client.budget_status()
        write_json_atomic(proposal_output, artifact)
    if "status" not in artifact:
        artifact["status"] = "complete" if not artifact["failures"] else "partial"
        write_json_atomic(proposal_output, artifact)
    click.echo(
        f"Wrote {len(artifact['papers'])} paper results and "
        f"{len(artifact['failures'])} failures to {proposal_output}"
    )


if __name__ == "__main__":
    main()
