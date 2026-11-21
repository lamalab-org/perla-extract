"""Compare local Docling pipelines through the production evidence adapter.

This experiment makes no extraction-model calls. Literal coverage is a diagnostic
against existing reviewed values, not a measurement of extraction accuracy.
Run with --help, or read README.md beside this file.
"""

from __future__ import annotations

import csv
import importlib.metadata
import io
import json
import logging
import math
import os
import platform
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # Windows does not expose POSIX process resource counters.
    resource = None

import click
import pymupdf

from perla_extract.study_extraction import source as source_adapter
from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.reference_io import load_reference
from perla_extract.study_extraction.source import (
    _docling_evidence_blocks,
    _parser_implementation_sha256,
    _scientific_evidence_blocks,
    _sha256,
)
from perla_extract.study_extraction.spans import evidence_payload

MODEL = "ibm-granite/granite-docling-258M"
REVISION = "982fe3b40f2fa73c365bdb1bcacf6c81b7184bfe"
CONDITIONS = ("standard", "granite", "granite_native")
MAX_OUTPUT_TOKENS = 8192
SECONDS_PER_PAGE = 180
LOCATION = re.compile(r"^(main|supplement)_p(\d+)_")


def literal_present(literal: str, text: str) -> bool:
    """Ignore layout whitespace, retaining case, signs and decimal spelling.

    Word boundaries prevent 8.4 from matching 18.4 or 8.40. This deliberately
    does not convert units or recognize equivalent scientific notation.
    """

    parts = re.split(r"\s+", literal.strip())
    if not parts or not parts[0]:
        return False
    pattern = r"\s*".join(re.escape(part) for part in parts)
    # Units may directly follow numbers (20%, 100mW/cm2). Numeric values must
    # still retain their sign and must not match part of another number.
    prefix = (
        r"(?<![\w.+−-])"
        if parts[0][0].isdigit() or parts[0][0] in "+−-"
        else r"(?<![A-Za-z])"
        if parts[0][0].isalpha()
        else ""
    )
    suffix = (
        r"(?![\w.])"
        if parts[-1][-1].isdigit()
        else r"(?![A-Za-z])"
        if parts[-1][-1].isalpha()
        else ""
    )
    return re.search(prefix + pattern + suffix, text) is not None


def reference_probes(value: Any, path: str = "") -> list[dict]:
    """Locate reviewed literal values and units using their existing citations.

    Only literals actually present in a cited quote become probes. A probe is
    checked on that source page, never elsewhere in the document. These are
    maintenance diagnostics; equivalent formatting can still miss a probe.
    """

    probes: list[dict] = []
    if isinstance(value, dict):
        for evidence in value.get("evidence", []):
            location = LOCATION.match(evidence.get("block_id", ""))
            if not location:
                continue
            for key in ("raw_value", "unit"):
                literal = value.get(key)
                if isinstance(literal, str) and literal_present(
                    literal, evidence.get("quote", "")
                ):
                    probes.append(
                        {
                            "path": f"{path}/{key}",
                            "literal": literal,
                            "source": location[1],
                            "page": int(location[2]),
                        }
                    )
        for key, child in value.items():
            probes.extend(reference_probes(child, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            probes.extend(reference_probes(child, f"{path}/{index}"))
    return list({json.dumps(probe, sort_keys=True): probe for probe in probes}.values())


def audit(blocks: list[EvidenceBlock], job: dict) -> tuple[dict, list[dict]]:
    """Check source geometry and reviewed-literal visibility, without scoring."""

    with pymupdf.open(job["pdf"]) as pdf:
        sizes = {i + 1: (p.rect.width, p.rect.height) for i, p in enumerate(pdf)}
        native = {i + 1: p.get_text() for i, p in enumerate(pdf)}
    start, end = job["page_range"]
    page_text: dict[int, str] = {}
    invalid_geometry = 0
    for block in blocks:
        if block.source != job["source"] or not start <= block.page <= end:
            raise ValueError(
                "adapter returned evidence outside the requested source/pages"
            )
        page_text[block.page] = page_text.get(block.page, "") + "\n" + block.text
        if block.bbox is not None:
            x0, y0, x1, y1 = block.bbox
            width, height = sizes[block.page]
            invalid_geometry += not (
                all(math.isfinite(v) for v in block.bbox)
                and -1 <= x0 <= x1 <= width + 1
                and -1 <= y0 <= y1 <= height + 1
            )
    identifiers = [block.block_id for block in blocks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("adapter returned duplicate evidence identifiers")
    present = set(identifiers)
    dangling = sum(
        target not in present
        for block in blocks
        for target in block.metadata.get("context_block_ids", [])
    )
    checked = [
        probe
        | {
            "found": literal_present(
                probe["literal"], page_text.get(probe["page"], "")
            ),
            "native_found": literal_present(probe["literal"], native[probe["page"]]),
        }
        for probe in job["probes"]
        if probe["source"] == job["source"] and start <= probe["page"] <= end
    ]
    encoded = json.dumps(evidence_payload(blocks), ensure_ascii=False).encode()
    return {
        "blocks": len(blocks),
        "tables": sum(b.kind == "table" for b in blocks),
        "pages_with_evidence": len(page_text),
        "missing_geometry": sum(b.bbox is None for b in blocks),
        "invalid_geometry": invalid_geometry,
        "dangling_context_links": dangling,
        "evidence_bytes": len(encoded),
        "approximate_evidence_tokens": math.ceil(len(encoded) / 3),
        "literal_probes": len(checked),
        "literal_probes_found": sum(p["found"] for p in checked),
        "native_visible_probes": sum(p["native_found"] for p in checked),
        "native_visible_probes_lost": sum(
            p["native_found"] and not p["found"] for p in checked
        ),
    }, checked


def converter_for(condition: str, device: str):
    """Fix one model, runtime and decoding policy; vary only text sourcing."""

    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        VlmConvertOptions,
        VlmPipelineOptions,
    )
    from docling.datamodel.vlm_engine_options import TransformersVlmEngineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    accelerator = AcceleratorOptions(device=device, num_threads=4)
    if condition == "standard":
        options = PdfPipelineOptions(accelerator_options=accelerator)
        pdf_option = PdfFormatOption(pipeline_options=options)
    else:
        vlm = VlmConvertOptions.from_preset(
            "granite_docling",
            engine_options=TransformersVlmEngineOptions(
                device=device,
                load_in_8bit=False,
                compile_model=False,
            ),
        )
        vlm.model_spec.revision = REVISION
        vlm.model_spec.temperature = 0.0
        vlm.model_spec.max_new_tokens = MAX_OUTPUT_TOKENS
        vlm.force_backend_text = condition == "granite_native"
        options = VlmPipelineOptions(
            accelerator_options=accelerator,
            vlm_options=vlm,
            force_backend_text=vlm.force_backend_text,
        )
        pdf_option = PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=options)
    return DocumentConverter(format_options={InputFormat.PDF: pdf_option}), options


def conversion_complete(
    status: str, pages: list[dict], expected: int, *, requires_vlm: bool
) -> bool:
    """Reject missing pages and incomplete generation, including unknown stop reasons.

    Some runtimes return an unspecified stop reason. Reaching the token allowance
    still makes that page incomplete; successful parsing alone cannot clear it.
    """

    return (
        status == "success"
        and len(pages) == expected
        and not any(
            p["stop_reason"] in {"length", "inference_error", "content_filtered"}
            or (p["tokens"] is not None and p["tokens"] >= MAX_OUTPUT_TOKENS)
            or (requires_vlm and not p["raw_output"])
            for p in pages
        )
    )


def worker(job_path: Path) -> None:
    """Save partial results and diagnostics; the parent enforces a hard timeout."""

    import torch
    from docling.datamodel.base_models import InputFormat
    from docling_core.types.doc import ContentLayer, DocItemLabel

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    job = json.loads(job_path.read_text())
    if _sha256(Path(job["pdf"])) != job["source_sha256"]:
        raise ValueError("PDF changed after planning")
    directory = job_path.parent
    torch.manual_seed(0)
    begin = time.monotonic()
    converter, options = converter_for(job["condition"], job["device"])
    write_json_atomic(directory / "options.json", options.model_dump(mode="json"))
    converter.initialize_pipeline(InputFormat.PDF)
    loaded = time.monotonic()
    result = converter.convert(
        job["pdf"], page_range=tuple(job["page_range"]), raises_on_error=False
    )
    converted = time.monotonic()
    result.document.save_as_json(directory / "docling.json")
    all_blocks = _docling_evidence_blocks(
        result.document,
        Path(job["pdf"]),
        job["source"],
        content_layer_type=ContentLayer,
        item_label=DocItemLabel,
    )
    blocks, excluded = _scientific_evidence_blocks(all_blocks)
    write_json_atomic(
        directory / "document.json",
        {
            "blocks": [b.model_dump(mode="json") for b in blocks],
        },
    )
    pages = []
    for page in result.pages:
        response = page.predictions.vlm_response
        pages.append(
            {
                "page": page.page_no,
                "stop_reason": response.stop_reason.value if response else None,
                "tokens": response.num_tokens if response else None,
                "raw_output": response.text if response else None,
            }
        )
    write_json_atomic(directory / "vlm-pages.json", pages)
    metrics, probes = audit(blocks, job)
    write_json_atomic(directory / "literal-probes.json", probes)
    complete = conversion_complete(
        result.status.value,
        pages,
        job["page_range"][1] - job["page_range"][0] + 1,
        requires_vlm=job["condition"] != "standard",
    )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
    write_json_atomic(
        directory / "metrics.json",
        metrics
        | {
            "status": "complete" if complete else "partial",
            "conversion_status": result.status.value,
            "errors": [e.model_dump(mode="json") for e in result.errors],
            "excluded_blocks": excluded,
            "requested_pages": job["page_range"][1] - job["page_range"][0] + 1,
            "model_load_seconds": loaded - begin,
            "conversion_seconds": converted - loaded,
            "total_seconds": time.monotonic() - begin,
            "peak_rss_mb": rss / (1024**2 if sys.platform == "darwin" else 1024)
            if rss is not None
            else None,
            "provider_requests": 0,
        },
    )


def make_plan(
    papers_file: Path,
    device: str,
    page_range: tuple[int, int] | None,
    timeout: int,
    seconds_per_page: int = SECONDS_PER_PAGE,
) -> dict:
    """Seal inputs and resource limits before comparing complete documents."""

    papers = json.loads(papers_file.read_text())
    if not isinstance(papers, list) or not papers:
        raise ValueError("papers must be a nonempty JSON list")
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "docling",
            "docling-core",
            "transformers",
            "torch",
            "PyMuPDF",
        )
    }
    jobs, identifiers = [], set()
    for paper in papers:
        required = {"paper_id", "pdf", "reference"}
        if (
            not isinstance(paper, dict)
            or not required <= paper.keys()
            or paper.keys() - required - {"supplement"}
        ):
            raise ValueError(
                "each paper needs paper_id, pdf, reference and optionally supplement"
            )
        paper_id = paper["paper_id"]
        if (
            not isinstance(paper_id, str)
            or paper_id in identifiers
            or not re.fullmatch(r"[\w.-]+", paper_id)
        ):
            raise ValueError(f"duplicate or unsafe paper_id: {paper_id}")
        identifiers.add(paper_id)
        reference_dir = (papers_file.parent / paper["reference"]).resolve()
        _, benchmark = load_reference(reference_dir)
        if benchmark.paper_id != paper_id:
            raise ValueError(f"paper ID does not match reviewed reference: {paper_id}")
        source_hashes = [
            _sha256((papers_file.parent / paper[key]).resolve())
            for key in ("pdf", "supplement")
            if paper.get(key)
        ]
        if sorted(source_hashes) != sorted(benchmark.source_sha256):
            raise ValueError(
                f"PDF/SI do not match the reviewed source hashes: {paper_id}"
            )
        reference = reference_dir / "ground_truth.json"
        probes = reference_probes(json.loads(reference.read_text()))
        for role, key in (("main", "pdf"), ("supplement", "supplement")):
            if not paper.get(key):
                continue
            pdf = (papers_file.parent / paper[key]).resolve()
            with pymupdf.open(pdf) as document:
                pages = page_range or (1, len(document))
                if not 1 <= pages[0] <= pages[1] <= len(document):
                    raise ValueError(f"invalid page range for {pdf}")
            for condition in CONDITIONS:
                jobs.append(
                    {
                        "paper_id": paper_id,
                        "source": role,
                        "pdf": str(pdf),
                        "source_sha256": _sha256(pdf),
                        "reference": str(reference),
                        "reference_sha256": _sha256(reference),
                        "probes": probes,
                        "condition": condition,
                        "device": device,
                        "page_range": list(pages),
                        "timeout_seconds": max(
                            timeout, (pages[1] - pages[0] + 1) * seconds_per_page
                        ),
                    }
                )
    jobs = order_paired_jobs(jobs)
    from huggingface_hub.constants import HF_HUB_CACHE

    cache = Path(HF_HUB_CACHE)
    model_refs = {}
    for model in (
        "docling-project/docling-models",
        "docling-project/docling-layout-heron",
        MODEL,
    ):
        model_refs[model] = {
            str(p.relative_to(cache)): p.read_text().strip()
            for p in sorted(
                (cache / ("models--" + model.replace("/", "--")) / "refs").rglob("*")
            )
            if p.is_file()
        }
    return {
        "model": MODEL,
        "model_revision": REVISION,
        "device": device,
        "conditions": CONDITIONS,
        "timeout_seconds": timeout,
        "seconds_per_page": seconds_per_page,
        "execution_order": "shortest papers first; all conditions together per source; shuffled condition order, seed 20261121",
        "jobs": jobs,
        "software_versions": versions,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "adapter_sha256": _parser_implementation_sha256(),
        "experiment_sha256": _sha256(Path(__file__)),
        "papers_sha256": _sha256(papers_file),
        "cached_model_refs": model_refs,
        "provider_requests": 0,
    }


def order_paired_jobs(jobs: list[dict]) -> list[dict]:
    """Complete comparisons paper by paper, starting with smaller inputs.

    Paper length uses all supplied main/SI pages. Conditions are shuffled within
    each source so one parser does not always receive the first execution slot.
    Early results consequently represent shorter papers, not the whole corpus.
    """

    groups: dict[tuple[str, str], list[dict]] = {}
    pages: dict[str, int] = {}
    for job in jobs:
        key = (job["paper_id"], job["source"])
        if key not in groups:
            pages[key[0]] = (
                pages.get(key[0], 0) + job["page_range"][1] - job["page_range"][0] + 1
            )
        groups.setdefault(key, []).append(job)
    ordered = []
    randomizer = random.Random(20261121)
    for key in sorted(groups, key=lambda k: (pages[k[0]], k[0], k[1] != "main")):
        variants = groups[key].copy()
        randomizer.shuffle(variants)
        ordered.extend(variants)
    return ordered


def save_progress(rows: list[dict], output: Path, planned: int) -> None:
    """Publish readable results after every finished attempt, including failures."""

    columns = list(
        dict.fromkeys(
            ["paper_id", "source", "condition", *(key for row in rows for key in row)]
        )
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    temporary = output / "results.csv.tmp"
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(output / "results.csv")
    write_json_atomic(
        output / "summary.json",
        {
            "status": "finished" if len(rows) == planned else "running",
            "planned": planned,
            "attempted": len(rows),
            "complete": sum(row["status"] == "complete" for row in rows),
            "provider_requests": 0,
            "default_decision": "requires_downstream_extraction_comparison",
            "note": "Literal visibility and parser success are not extraction precision or recall.",
        },
    )


def execute(plan: dict, output: Path) -> None:
    """Run one isolated conversion at a time; retain every failure in the table."""

    rows = []
    save_progress(rows, output, len(plan["jobs"]))
    for number, job in enumerate(plan["jobs"], 1):
        if plan.get("experiment_sha256") != _sha256(Path(__file__)) or plan.get(
            "adapter_sha256"
        ) != _sha256(Path(source_adapter.__file__)):
            raise ValueError(
                "experiment code changed after planning; use a fresh output directory"
            )
        directory = output / job["paper_id"] / job["source"] / job["condition"]
        directory.mkdir(parents=True, exist_ok=True)
        job_path = directory / "job.json"
        metrics_path = directory / "metrics.json"
        if not metrics_path.exists():
            write_json_atomic(job_path, job)
            click.echo(
                f"[{number}/{len(plan['jobs'])}] {job['paper_id']} {job['source']} {job['condition']}"
            )
            with (directory / "conversion.log").open("w") as log:
                try:
                    process = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "examples.parsing.compare",
                            "--worker",
                            str(job_path),
                        ],
                        env=dict(
                            os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"
                        ),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=job.get("timeout_seconds", plan["timeout_seconds"]),
                    )
                    if process.returncode or not metrics_path.exists():
                        raise RuntimeError(
                            f"worker exited {process.returncode}; see conversion.log"
                        )
                except (subprocess.TimeoutExpired, RuntimeError) as exc:
                    write_json_atomic(
                        metrics_path,
                        {
                            "status": "timeout"
                            if isinstance(exc, subprocess.TimeoutExpired)
                            else "failed",
                            "error": str(exc),
                            "provider_requests": 0,
                        },
                    )
        metrics = json.loads(metrics_path.read_text())
        rows.append(
            {key: job[key] for key in ("paper_id", "source", "condition")}
            | {key: value for key, value in metrics.items() if key != "errors"}
        )
        save_progress(rows, output, len(plan["jobs"]))


@click.command()
@click.option("--papers", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path))
@click.option(
    "--device",
    type=click.Choice(["cpu", "cuda"]),
    default="cpu",
    show_default=True,
)
@click.option(
    "--page-range",
    type=(int, int),
    default=None,
    help="Inclusive PDF pages; omit for complete documents.",
)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=900,
    show_default=True,
    help="Fixed limit, or minimum seconds when --seconds-per-page is supplied.",
)
@click.option(
    "--seconds-per-page",
    type=click.IntRange(min=0),
    default=SECONDS_PER_PAGE,
    show_default=True,
    help="Scale each document's limit with page count, with --timeout as its minimum.",
)
@click.option(
    "--plan-only",
    is_flag=True,
    help="Validate inputs and save the plan without loading models.",
)
@click.option(
    "--worker", "worker_path", type=click.Path(exists=True, path_type=Path), hidden=True
)
def main(
    papers,
    output,
    device,
    page_range,
    timeout,
    seconds_per_page,
    plan_only,
    worker_path,
):
    """Compare standard Docling, GraniteDocling, and Granite with native text."""

    if worker_path:
        worker(worker_path)
        return
    if papers is None or output is None:
        raise click.UsageError("--papers and --output are required")
    try:
        plan = make_plan(
            papers.resolve(), device, page_range, timeout, seconds_per_page
        )
        output = output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        path = output / "plan.json"
        if path.exists() and json.loads(path.read_text()) != json.loads(
            json.dumps(plan)
        ):
            raise ValueError(
                "plan differs from existing output; choose a new output directory"
            )
        write_json_atomic(path, plan)
        if not plan_only:
            execute(plan, output)
        click.echo(str(path if plan_only else output / "results.csv"))
    except (
        OSError,
        ValueError,
        KeyError,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
