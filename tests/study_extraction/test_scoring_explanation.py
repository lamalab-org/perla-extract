"""Hand-checked explanations must describe the same decisions as the score ledger."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction import evaluation, scoring_facts
from perla_extract.study_extraction.evaluation import (
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.evaluation_cli import main
from perla_extract.study_extraction.models import (
    ReportedValue,
    StabilityTest,
    StudyExtraction,
)
from perla_extract.study_extraction.scoring_explanation import FactSelection

EVIDENCE = [{"block_id": "synthetic", "quote": "Invented scoring fixture."}]
METRIC = "/performance_observations/0/metrics/0"


def quantity(number=20, unit="%", name="PCE"):
    return dict(
        name=name,
        raw_value=str(number),
        value_number=float(number),
        unit=unit,
        evidence=EVIDENCE,
    )


def study():
    return StudyExtraction.model_validate(
        {
            "paper": {"title": "Invented scoring fixture", "doi": None},
            "device_families": [
                {
                    "family_id": "family",
                    "label": "control",
                    "variant": None,
                    "architecture": "ITO/perovskite/Ag",
                    "polarity": "p-i-n",
                    "full_stack_raw": "ITO/perovskite/Ag",
                    "layers": [],
                    "absorbers": [],
                    "processing_steps": [],
                    "evidence": EVIDENCE,
                }
            ],
            "individual_devices": [
                {
                    "device_id": "cell",
                    "family_id": "family",
                    "label": "control",
                    "variant": None,
                    "champion_status": "yes",
                    "selection_basis": "champion",
                    "evidence": EVIDENCE,
                }
            ],
            "performance_observations": [
                {
                    "observation_id": "reverse",
                    "device_id": "cell",
                    "measurement_type": "jv_scan",
                    "scan_direction": "reverse",
                    "metrics": [quantity()],
                    "evidence": EVIDENCE,
                }
            ],
            "population_statistics": [],
            "stability_tests": [],
            "unresolved_notes": [],
        }
    )


def explain(truth, prediction, *, path=METRIC, side="truth", view="strict", **kwargs):
    report, explanation = evaluate_study_with_explanations(
        truth,
        prediction,
        facts=[FactSelection(side=side, path=path, view=view)],
        **kwargs,
    )
    assert report == evaluate_study(truth, prediction, config=kwargs.get("config"))
    assert explanation.truth_content_sha256 == report.truth_content_sha256
    assert explanation.prediction_content_sha256 == report.prediction_content_sha256
    return report, explanation.facts[0]


def test_equivalent_claim_explains_canonical_values_and_selected_paths():
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics[0] = ReportedValue.model_validate(
        quantity(0.2, "dimensionless")
    )
    report, fact = explain(truth, prediction)
    assert report.core_facts.groups["performance"].matched == 1
    assert fact.reason == "matched"
    candidate = fact.candidates[0]
    assert candidate.truth_path == candidate.predicted_path == METRIC
    assert candidate.truth_claim["canonical"] == candidate.predicted_claim["canonical"]
    assert candidate.selected and candidate.value_equal and candidate.context_equal


def test_changed_value_has_no_strict_credit_and_explains_both_claims():
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics[0] = ReportedValue.model_validate(
        quantity(21)
    )
    report, fact = explain(truth, prediction)
    assert report.core_facts.groups["performance"].matched == 0
    assert fact.reason == "value_mismatch"
    assert fact.candidates[0].truth_claim["number"] == 20
    assert fact.candidates[0].predicted_claim["number"] == 21


def test_wrong_scan_reports_context_field_but_value_view_still_matches():
    truth, prediction = study(), study()
    prediction.performance_observations[0].scan_direction = "forward"
    _, fact = explain(truth, prediction)
    assert fact.reason == "context_mismatch"
    difference = fact.candidates[0].context_mismatches[0]
    assert difference.component == "scan_direction"
    assert (
        difference.truth_path
        == difference.predicted_path
        == "/performance_observations/0/scan_direction"
    )
    assert difference.truth_value == "reverse"
    assert difference.predicted_value == "forward"
    _, recovered = explain(truth, prediction, view="value")
    assert recovered.reason == "matched"
    assert not recovered.candidates[0].context_equal


def test_missing_property_and_missing_owner_are_distinct():
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics[0].name = "FF"
    _, fact = explain(truth, prediction)
    assert fact.reason == "property_mismatch"
    assert fact.candidate_count == 0
    assert fact.nearby_properties[0].path == METRIC
    assert fact.nearby_properties[0].name == "ff"
    prediction.performance_observations.clear()
    _, fact = explain(truth, prediction)
    assert fact.reason == "owner_unmatched"
    assert not fact.owner_matched


def test_eligible_duplicate_loses_credit_to_exact_competing_path():
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics.append(
        prediction.performance_observations[0].metrics[0].model_copy(deep=True)
    )
    paths = [METRIC, "/performance_observations/0/metrics/1"]
    report, explanation = evaluate_study_with_explanations(
        truth,
        prediction,
        facts=[FactSelection(side="prediction", path=p) for p in paths],
    )
    assert report.core_facts.groups["performance"].matched == 1
    won = next(f for f in explanation.facts if f.reason == "matched")
    lost = next(f for f in explanation.facts if f.reason == "one_to_one_competition")
    assert lost.candidates[0].eligible
    assert lost.candidates[0].competing_predicted_path == won.selection.path


def test_record_ambiguity_blocks_primary_but_not_value_credit():
    truth, prediction = study(), study()
    duplicate = prediction.performance_observations[0].model_copy(deep=True)
    duplicate.observation_id = "duplicate"
    prediction.performance_observations.append(duplicate)
    _, fact = explain(truth, prediction)
    assert fact.reason == "ambiguity"
    assert fact.candidates[0].ambiguity_blocked
    assert {s.path for s in fact.candidates[0].ambiguity_sources} == {
        "/performance_observations/0"
    }
    _, value = explain(truth, prediction, view="value")
    assert value.reason == "matched"


def test_stability_context_names_the_checkpoint_field_and_preserves_unordered_conditions():
    truth = study()
    truth.stability_tests = [
        StabilityTest.model_validate(
            {
                "test_id": "aging",
                "family_id": "family",
                "device_id": "cell",
                "specimen_label": "control",
                "link_status": "explicit_device_link",
                "conditions": [
                    quantity(65, "°C", "temperature"),
                    quantity(1, "sun", "illumination"),
                ],
                "checkpoints": [
                    {
                        "checkpoint_id": "end",
                        "time": quantity(100, "h", "time"),
                        "conditions": [],
                        "outcomes": [quantity(80, "%", "retained PCE")],
                        "evidence": EVIDENCE,
                    }
                ],
                "evidence": EVIDENCE,
            }
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].conditions.reverse()
    path = "/stability_tests/0/checkpoints/0/outcomes/0"
    _, fact = explain(truth, prediction, path=path)
    assert fact.reason == "matched"
    prediction.stability_tests[0].checkpoints[0].time = ReportedValue.model_validate(
        quantity(101, "h", "time")
    )
    _, fact = explain(truth, prediction, path=path)
    assert fact.reason == "matched"  # Within the default 1% context tolerance.
    prediction.stability_tests[0].checkpoints[0].time = ReportedValue.model_validate(
        quantity(102, "h", "time")
    )
    _, fact = explain(truth, prediction, path=path)
    assert fact.reason == "context_mismatch"
    assert (
        fact.candidates[0].context_mismatches[0].truth_path
        == "/stability_tests/0/checkpoints/0/time"
    )


def test_explanation_observes_the_same_single_evaluation_and_bounds_candidates():
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics *= 4
    with patch.object(
        evaluation, "compare_facts", wraps=evaluation.compare_facts
    ) as compare:
        plain = evaluate_study(truth, prediction)
        calls = compare.call_count
    with patch.object(
        evaluation, "compare_facts", wraps=evaluation.compare_facts
    ) as compare:
        report, explanation = evaluate_study_with_explanations(
            truth,
            prediction,
            facts=[FactSelection(side="truth", path=METRIC)],
            max_candidates=2,
        )
        assert compare.call_count == calls
    assert report == plain
    fact = explanation.facts[0]
    assert fact.candidate_count == 4
    assert fact.candidates_truncated and len(fact.candidates) == 2
    assert fact.candidates[0].selected


def test_ordinary_scoring_does_not_collect_context_diagnostics():
    truth, prediction = study(), study()
    for value in (truth, prediction):
        value.performance_observations[0].metrics.append(
            ReportedValue.model_validate(quantity(80, "%", "FF"))
        )
    with patch.object(
        scoring_facts, "compare_context", wraps=scoring_facts.compare_context
    ) as compare:
        evaluate_study(truth, prediction)
        assert compare.call_count == 0
        evaluate_study_with_explanations(
            truth, prediction, facts=[FactSelection(side="truth", path=METRIC)]
        )
        assert compare.call_count == 1


def test_ambiguity_explanation_retains_the_other_indistinguishable_scope():
    truth = study()
    truth.stability_tests = [
        StabilityTest.model_validate(
            {
                "test_id": "aging",
                "family_id": "family",
                "device_id": "cell",
                "specimen_label": "control",
                "link_status": "explicit_device_link",
                "conditions": [],
                "checkpoints": [
                    {
                        "checkpoint_id": "end",
                        "time": quantity(100, "h", "time"),
                        "conditions": [],
                        "outcomes": [quantity(80, "%", "retained PCE")],
                        "evidence": EVIDENCE,
                    }
                ],
                "evidence": EVIDENCE,
            }
        )
    ]
    prediction = truth.model_copy(deep=True)
    checkpoints = prediction.stability_tests[0].checkpoints
    duplicate = checkpoints[0].model_copy(deep=True)
    duplicate.checkpoint_id = "duplicate"
    checkpoints.append(duplicate)
    other = checkpoints[0].model_copy(deep=True)
    other.checkpoint_id = "different"
    other.time = ReportedValue.model_validate(quantity(200, "h", "time"))
    other.outcomes = [ReportedValue.model_validate(quantity(90, "%", "retained PCE"))]
    checkpoints.append(other)
    _, fact = explain(
        truth, prediction, path="/stability_tests/0/checkpoints/0/outcomes/0"
    )
    assert fact.reason == "ambiguity"
    candidate = fact.candidates[0]
    assert (
        candidate.value_equal
        and candidate.context_equal
        and candidate.ambiguity_blocked
    )
    assert {(s.side, s.path) for s in candidate.ambiguity_sources} == {
        ("prediction", "/stability_tests/0/checkpoints/0"),
        ("prediction", "/stability_tests/0/checkpoints/1"),
    }


@pytest.mark.parametrize(
    "paths,maximum,message",
    [
        ([], 5, "between 1 and 25"),
        ([FactSelection(side="truth", path=METRIC)], 21, "between 1 and 20"),
        ([FactSelection(side="truth", path="/paper/title")], 5, "Not a scored"),
    ],
)
def test_invalid_explanation_request_is_explicit(paths, maximum, message):
    with pytest.raises(ValueError, match=message):
        evaluate_study_with_explanations(
            study(), study(), facts=paths, max_candidates=maximum
        )


def test_cli_keeps_report_schema_and_writes_separate_explanation(tmp_path):
    truth = tmp_path / "truth.json"
    prediction = tmp_path / "prediction.json"
    output = tmp_path / "score.json"
    explanation = tmp_path / "why.json"
    truth.write_text(study().model_dump_json())
    prediction.write_text(study().model_dump_json())
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
            "--explain-truth",
            METRIC,
            "--explanation-output",
            str(explanation),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == evaluate_study(
        study(), study()
    ).model_dump(mode="json")
    assert json.loads(explanation.read_text())["facts"][0]["reason"] == "matched"
