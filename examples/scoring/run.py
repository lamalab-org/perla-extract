"""Exercise correction, frozen export, and scoring without papers or model calls.

All source text and review actions are synthetic. Expected counts are maintained
separately from the scorer so a changed scoring rule cannot bless its own result.
The normal review store and CLI are used to detect integration drift as well as
errors in scientific credit. Run from a checkout with ``PYTHONPATH=.:src``.
"""

from __future__ import annotations

import json
import platform
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import click

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.evaluation_cli import main as evaluate
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    PerformanceObservation,
    ReportedValue,
    StudyExtraction,
)
from review_workbench.ground_truth_export import (
    build_ground_truth_export,
    write_ground_truth_export,
)
from review_workbench.study_review import (
    RECORD_IDENTIFIERS,
    InventoryAuditRequest,
    MutationRequest,
    RecordDecisionRequest,
    StageRequest,
    StudyReviewStore,
)

EXPECTED = Path(__file__).with_name("expected.json")
SPLIT = "dev"
PAPER = "10.0000--synthetic"
REVIEWER = "synthetic-reviewer-not-a-person"
TEXT = (
    "Synthetic example, not a published experiment. The finished stack is "
    "ITO/perovskite/Ag; DMF is a processing solvent, not a finished layer. "
    "The champion control cell has reverse-scan PCE 20% and FF 80%. "
    "The same PCE as a fraction is 0.20. "
    "A discarded preliminary estimate was 21%, not the final cell efficiency."
)
EVIDENCE = [EvidenceCitation(block_id="main-p1", quote=TEXT)]


def quantity(name: str, number: float) -> ReportedValue:
    return ReportedValue(
        name=name,
        raw_value=f"{number:g}%",
        value_number=number,
        unit="%",
        evidence=EVIDENCE,
    )


def seed_study() -> StudyExtraction:
    """Include a wrong stack, wrong PCE, and missing FF for a known error ledger."""

    return StudyExtraction(
        paper=PaperMetadata(title="Synthetic scoring example", doi=None),
        device_families=[
            DeviceFamily(
                family_id="family",
                label="Control",
                variant=None,
                architecture=None,
                polarity="not_reported",
                full_stack_raw="ITO/DMF/perovskite/Ag",
                layers=[],
                absorbers=[],
                processing_steps=[],
                evidence=EVIDENCE,
            )
        ],
        individual_devices=[
            IndividualDevice(
                device_id="cell",
                family_id="family",
                label="Champion control",
                variant=None,
                champion_status="yes",
                selection_basis="champion",
                evidence=EVIDENCE,
            )
        ],
        performance_observations=[
            PerformanceObservation(
                observation_id="reverse",
                device_id="cell",
                measurement_type="jv_scan",
                scan_direction="reverse",
                metrics=[quantity("PCE", 21)],
                evidence=EVIDENCE,
            )
        ],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def build_example(output: Path) -> dict:
    """Save simulated corrections through the review API and score fixed variants.

    Refuse existing destinations: review events and frozen exports are evidence,
    not disposable caches. Each run gets its own timestamps and hashes; scientific
    counts and the checked configuration must remain identical.
    """

    output.mkdir(parents=True, exist_ok=False)
    document = {
        "blocks": [
            EvidenceBlock(
                block_id="main-p1",
                source="main",
                page=1,
                kind="text",
                text=TEXT,
            ).model_dump(mode="json")
        ]
    }
    seed = seed_study()
    store = StudyReviewStore(output / "review")
    bundle = store.import_seed(
        SPLIT,
        PAPER,
        seed.model_dump(mode="json"),
        document=document,
        manifest={"synthetic": True, "source_scope": "invented paragraph only"},
        reviewer_id=REVIEWER,
    )
    corrections = [
        ("replace", "/device_families/0/full_stack_raw", "ITO/perovskite/Ag"),
        (
            "replace",
            "/performance_observations/0/metrics/0",
            quantity("PCE", 20).model_dump(mode="json"),
        ),
        (
            "add",
            "/performance_observations/0/metrics/-",
            quantity("FF", 80).model_dump(mode="json"),
        ),
    ]
    for action, path, value in corrections:
        bundle = store.mutate(
            SPLIT,
            PAPER,
            MutationRequest(
                action=action,
                path=path,
                value=value,
                base_revision=bundle["revision"],
                evidence=[citation.model_dump(mode="json") for citation in EVIDENCE],
            ),
            REVIEWER,
        )
    bundle = store.inventory_audit(
        SPLIT,
        PAPER,
        InventoryAuditRequest(
            base_revision=bundle["revision"],
            searched_sources=["main"],
            expected_counts={},
        ),
        REVIEWER,
    )
    bundle = store.complete_stage(
        SPLIT,
        PAPER,
        StageRequest(
            stage="inventory",
            base_revision=bundle["revision"],
        ),
        REVIEWER,
    )
    for collection, identifier in RECORD_IDENTIFIERS.items():
        for record in bundle["ground_truth"].get(collection, []):
            bundle = store.decide_record(
                SPLIT,
                PAPER,
                RecordDecisionRequest(
                    collection=collection,
                    record_id=record[identifier],
                    decision="verified",
                    base_revision=bundle["revision"],
                ),
                REVIEWER,
            )
    for stage in ("fields", "completeness", "adjudication"):
        bundle = store.complete_stage(
            SPLIT,
            PAPER,
            StageRequest(
                stage=stage,
                base_revision=bundle["revision"],
            ),
            REVIEWER,
        )
    export = build_ground_truth_export(store, SPLIT, PAPER)
    truth_path = write_ground_truth_export(export, output / "truth")
    write_json_atomic(truth_path / "document.json", document)
    equivalent = export.ground_truth.model_copy(deep=True)
    equivalent.performance_observations[0].metrics[0] = quantity("PCE", 20).model_copy(
        update={"raw_value": "0.20", "value_number": 0.2, "unit": "dimensionless"}
    )
    equivalent.performance_observations[0].metrics[0].evidence = [
        EvidenceCitation(
            block_id="main-p1", quote="The same PCE as a fraction is 0.20."
        )
    ]
    wrong_scan = export.ground_truth.model_copy(deep=True)
    wrong_scan.performance_observations[0].scan_direction = "forward"
    cases = {
        "seed": (seed, document),
        "corrected": (export.ground_truth, document),
        "equivalent_units": (equivalent, document),
        "wrong_scan": (wrong_scan, document),
    }
    expected = json.loads(EXPECTED.read_text())
    actual = {}
    for name, (prediction, evidence) in cases.items():
        started = perf_counter()
        run = output / "predictions" / name
        write_json_atomic(run / "extraction.json", prediction.model_dump(mode="json"))
        write_json_atomic(run / "document.json", evidence)
        write_json_atomic(
            run / "report.json",
            {
                "status": "synthetic_no_model_calls",
                "elapsed_seconds": perf_counter() - started,
                "usage": {
                    "live_calls": 0,
                    "cache_hits": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0,
                },
                "budget": {"provider_requests": 0, "cost_tracking_complete": True},
            },
        )
        report_path = run / "evaluation.json"
        evaluate.main(
            args=[
                "--truth",
                str(truth_path),
                "--prediction",
                str(run),
                "--output",
                str(report_path),
                "--fail-on-scoring-issues",
            ],
            standalone_mode=False,
        )
        report = json.loads(report_path.read_text())
        for key in ("format_version", "matcher_version"):
            if report[key] != expected[key]:
                raise ValueError(f"{key} changed; review the example contract")
        actual[name] = {
            group: {
                key: report["core_facts"]["groups"][group][key]
                for key in ("truth", "predicted", "matched")
            }
            for group in ("stack", "performance")
        }
        if report["config"] != expected["config"]:
            raise ValueError(
                "Scoring configuration changed; review the example contract"
            )
        if report["core_facts"]["profile"] != expected["profile"]:
            raise ValueError("Scoring profile changed; review the example contract")
    summary = {
        "synthetic": True,
        "actual": actual,
        "expected": expected["cases"],
        "passed": actual == expected["cases"],
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("pydantic", "pint", "click")},
    }
    write_json_atomic(output / "summary.json", summary)
    if not summary["passed"]:
        raise ValueError(
            f"Scoring differs from expected counts; inspect {output}/summary.json"
        )
    return summary


@click.command()
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="New directory for synthetic review, frozen truth, and scores.",
)
def main(output: Path) -> None:
    try:
        build_example(output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"PASS: four synthetic cases; inspect {output / 'summary.json'}")


if __name__ == "__main__":
    main()
