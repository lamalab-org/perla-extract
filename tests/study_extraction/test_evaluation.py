import hashlib
import json

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    _maximum_assignment,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_cli import main
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceCitation,
    IndividualDevice,
    Layer,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ReportedValue,
    StudyExtraction,
    study_schema_sha256,
)

EVIDENCE = [EvidenceCitation(block_id="b", quote="reported")]


def provenance(paper_id: str, split: str = "test") -> BenchmarkProvenance:
    """Build distinct, valid frozen-item identities for aggregation tests."""

    return BenchmarkProvenance(
        paper_id=paper_id,
        split=split,
        ground_truth_sha256=hashlib.sha256(f"truth:{paper_id}".encode()).hexdigest(),
        source_manifest_sha256=hashlib.sha256(
            f"source:{paper_id}".encode()
        ).hexdigest(),
        source_sha256=[hashlib.sha256(f"pdf:{paper_id}".encode()).hexdigest()],
    )


def value(raw: str = "20%", number: float = 20, unit: str = "%") -> ReportedValue:
    return ReportedValue(
        name="PCE",
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


def study(
    *, prefix: str = "truth", pce: ReportedValue | None = None
) -> StudyExtraction:
    family = DeviceFamily(
        family_id=f"{prefix}-family",
        label="control p-i-n device",
        variant="control",
        architecture="ITO/2PACz/perovskite/C60/Ag",
        polarity="p-i-n",
        full_stack_raw="ITO/2PACz/perovskite/C60/Ag",
        layers=[],
        absorbers=[],
        processing_steps=[],
        evidence=EVIDENCE,
    )
    device = IndividualDevice(
        device_id=f"{prefix}-device",
        family_id=family.family_id,
        label="champion control",
        variant="control",
        champion_status="yes",
        selection_basis="champion",
        evidence=EVIDENCE,
    )
    observation = PerformanceObservation(
        observation_id=f"{prefix}-observation",
        device_id=device.device_id,
        measurement_type="jv_scan",
        scan_direction="reverse",
        metrics=[pce or value()],
        evidence=EVIDENCE,
    )
    return StudyExtraction(
        paper=PaperMetadata(title="Paper", doi="10.1/example"),
        device_families=[family],
        individual_devices=[device],
        performance_observations=[observation],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def test_identical_science_with_different_ids_scores_perfectly():
    """Run-local identifiers must never reduce evaluation quality."""

    report = evaluate_study(study(prefix="truth"), study(prefix="prediction"))

    assert report.micro_inventory.f1 == 1
    assert report.field_agreement.reported_values.f1 == 1
    assert report.field_agreement.reported_value_accuracy == 1
    assert not report.unmatched_truth_record_keys
    assert not report.unmatched_prediction_record_keys


def test_extra_family_reduces_precision_without_reducing_recall():
    """Over-splitting must be visible as an inventory false positive."""

    truth = study()
    prediction = study(prefix="prediction")
    prediction.device_families.append(
        prediction.device_families[0].model_copy(
            update={"family_id": "extra", "label": "unrelated extra architecture"}
        )
    )

    report = evaluate_study(truth, prediction)

    family_score = report.inventory["device_families"]
    assert family_score.precision == 0.5
    assert family_score.recall == 1
    assert "device_families:extra" in report.unmatched_prediction_record_keys


def test_population_statistic_cannot_match_an_individual_observation():
    """Reporting levels are scored in separate inventories by construction."""

    truth = study()
    truth.population_statistics = [
        PopulationStatistic(
            population_id="population",
            family_id=truth.device_families[0].family_id,
            label="mean of 20 devices",
            statistic_type="mean",
            sample_size=20,
            metrics=[value()],
            evidence=EVIDENCE,
        )
    ]
    prediction = study(prefix="prediction")

    report = evaluate_study(truth, prediction)

    assert report.inventory["population_statistics"].recall == 0
    assert report.inventory["performance_observations"].precision == 1
    assert report.field_agreement.reported_values.recall == 0.5


def test_equivalent_units_are_equal_after_atomic_value_matching():
    truth = study(pce=value("0.20", 0.20, "dimensionless"))
    prediction = study(prefix="prediction", pce=value("20%", 20, "%"))

    report = evaluate_study(truth, prediction)

    assert report.field_agreement.reported_value_accuracy == 1


def test_relative_tolerance_does_not_become_one_absolute_unit_below_one():
    truth = study(pce=value("0.010 M", 0.010, "mol / liter"))
    prediction = study(prefix="prediction", pce=value("0.015 M", 0.015, "mol / liter"))

    report = evaluate_study(truth, prediction)

    assert report.field_agreement.reported_value_accuracy == 0


def test_uncertain_truth_masks_its_matching_prediction():
    """Reviewer abstention must not become either a false positive or false negative."""

    truth = study()
    prediction = study(prefix="prediction")
    report = evaluate_study(
        truth,
        prediction,
        ignored_truth_record_keys=["device_families:truth-family"],
    )

    assert report.inventory["device_families"].predicted == 0
    assert report.inventory["device_families"].truth == 0
    assert report.inventory["device_families"].f1 is None
    assert report.ignored_prediction_record_keys == [
        "device_families:prediction-family"
    ]


def test_parent_relationships_are_scored_separately_from_record_content():
    prediction = study(prefix="prediction")
    prediction.individual_devices[0].family_id = "wrong-family"

    report = evaluate_study(study(), prediction)

    assert report.inventory["individual_devices"].recall == 1
    assert report.field_agreement.relationships_compared > 0
    assert report.field_agreement.relationship_accuracy < 1


def test_reordering_schema_lists_does_not_change_scalar_agreement():
    """Layer order in JSON must not become an error when sequence retains the science."""

    truth = study()
    truth.device_families[0].layers = [
        Layer(
            layer_id="front",
            sequence=1,
            role="transparent_electrode",
            material="ITO",
            material_form="not_reported",
            reported_properties=[],
            evidence=EVIDENCE,
        ),
        Layer(
            layer_id="back",
            sequence=2,
            role="back_electrode",
            material="Ag",
            material_form="not_reported",
            reported_properties=[],
            evidence=EVIDENCE,
        ),
    ]
    prediction = study(prefix="prediction")
    prediction.device_families[0].layers = list(
        reversed(
            [
                layer.model_copy(update={"layer_id": f"prediction-{layer.layer_id}"})
                for layer in truth.device_families[0].layers
            ]
        )
    )

    report = evaluate_study(truth, prediction)

    assert report.field_agreement.scalar_field_accuracy == 1


def test_unknown_uncertainty_mask_key_is_rejected():
    with pytest.raises(ValueError, match="unknown truth records"):
        evaluate_study(
            study(),
            study(prefix="prediction"),
            ignored_truth_record_keys=["device_families:typo"],
        )


def test_global_assignment_avoids_greedy_order_errors():
    scores = [[0.9, 0.8], [0.85, 0.1]]

    assert sorted(_maximum_assignment(scores)) == [(0, 1), (1, 0)]


def test_cli_verifies_frozen_truth_manifest(tmp_path):
    payload = study().model_dump(mode="json")
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    truth = tmp_path / "truth"
    truth.mkdir()
    (truth / "ground_truth.json").write_text(json.dumps(payload), encoding="utf-8")
    (truth / "manifest.json").write_text(
        json.dumps(
            {
                "artifact_format_version": 2,
                "study_schema_sha256": study_schema_sha256(),
                "paper_id": "paper-a",
                "split": "test",
                "source_manifest": {"main": "source.pdf"},
                "files": {"ground_truth.json": hashlib.sha256(encoded).hexdigest()},
                "review": {"uncertain_record_keys": []},
            }
        ),
        encoding="utf-8",
    )
    prediction = tmp_path / "prediction.json"
    prediction.write_text(
        study(prefix="prediction").model_dump_json(), encoding="utf-8"
    )
    output = tmp_path / "evaluation.json"

    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    result_payload = json.loads(output.read_text())
    assert result_payload["micro_inventory"]["f1"] == 1
    assert result_payload["benchmark"]["paper_id"] == "paper-a"


def test_cli_attaches_evidence_validation_for_complete_prediction_run(tmp_path):
    truth = tmp_path / "truth.json"
    truth.write_text(study().model_dump_json(), encoding="utf-8")
    prediction = tmp_path / "prediction"
    prediction.mkdir()
    (prediction / "extraction.json").write_text(
        study(prefix="prediction").model_dump_json(), encoding="utf-8"
    )
    (prediction / "document.json").write_text(
        json.dumps(
            {
                "blocks": [
                    {
                        "block_id": "b",
                        "source": "main",
                        "page": 1,
                        "kind": "text",
                        "text": "reported 20%",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (prediction / "report.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "usage": {
                    "live_calls": 2,
                    "cache_hits": 0,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cost": 0.01,
                },
                "budget": {
                    "provider_requests": 2,
                    "cost_tracking_complete": True,
                },
                "elapsed_seconds": 3.5,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "evaluation.json"

    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    validation = json.loads(output.read_text())["prediction_validation"]
    assert validation["status"] == "verified"
    assert validation["issues"] == []
    efficiency = json.loads(output.read_text())["run_efficiency"]
    assert efficiency["total_tokens"] == 120
    assert efficiency["cost_usd"] == 0.01


def test_dataset_aggregation_uses_counts_and_skips_undefined_macro_rates():
    perfect = evaluate_study(study(), study(prefix="prediction"))
    empty = StudyExtraction(
        paper=PaperMetadata(title="Empty", doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    empty_report = evaluate_study(empty, empty)

    aggregate = aggregate_evaluations(
        [perfect, empty_report], bootstrap_samples=100, seed=7
    )

    assert aggregate.paper_count == 2
    assert aggregate.overall_micro.f1 == 1
    assert aggregate.overall_macro_f1.paper_count == 1
    assert aggregate.inventory_macro_f1["stability_tests"].mean is None
    assert aggregate.scalar_field_accuracy_micro.accuracy == 1
    assert aggregate.reported_values_micro.f1 == 1
    assert aggregate.reported_value_accuracy_micro.accuracy == 1
    assert aggregate.prediction_validation.paper_count == 0
    assert aggregate.efficiency.paper_count == 0


def test_dataset_aggregation_rejects_split_leakage_and_duplicate_sources():
    first = evaluate_study(
        study(), study(prefix="one"), benchmark=provenance("paper-a", "dev")
    )
    wrong_split = evaluate_study(
        study(), study(prefix="two"), benchmark=provenance("paper-b", "test")
    )
    duplicate_source = provenance("paper-c", "dev").model_copy(
        update={
            "source_sha256": first.benchmark.source_sha256 if first.benchmark else []
        }
    )
    same_source = evaluate_study(
        study(), study(prefix="three"), benchmark=duplicate_source
    )

    with pytest.raises(ValueError, match="mix benchmark splits"):
        aggregate_evaluations([first, wrong_split])
    with pytest.raises(ValueError, match="duplicate source"):
        aggregate_evaluations([first, same_source])


def test_dataset_aggregation_rejects_mixed_provenance_status():
    verified = evaluate_study(
        study(), study(prefix="verified"), benchmark=provenance("paper-a")
    )
    development = evaluate_study(study(), study(prefix="development"))

    with pytest.raises(ValueError, match="cannot mix provenance"):
        aggregate_evaluations([verified, development])
