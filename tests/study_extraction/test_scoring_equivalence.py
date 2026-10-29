"""Representation changes must preserve credit; changed scientific claims must not."""

import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError
from test_scoring_contracts import EVIDENCE, study

from perla_extract.study_extraction.evaluation import (
    EvaluationConfig,
    _record_features,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_cli import main
from perla_extract.study_extraction.models import ReportedValue
from perla_extract.study_extraction.scoring_facts import equal_value


def value(raw, unit=None, number=None):
    return ReportedValue(
        name="property",
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


@pytest.mark.parametrize(
    "left,right,expected",
    [
        (value("1–2 h", "h"), value("3600 to 7200 seconds", "s"), True),
        (value("1e-3–2e-3", "m"), value("1–2", "mm"), True),
        (value("-2--1", "°C"), value("271.15–272.15", "K"), True),
        (value("20 ± 1", "°C", 20), value("293.15 +/- 1", "K", 293.15), True),
        (value("68 ± 1.8", "degF", 68), value("20 ± 1", "°C", 20), True),
        (value("1–2"), value("1 to 2"), True),
        (value("1–2"), value("1–2", "dimensionless"), False),
        (value("1–2", "h"), value("60–121", "min"), False),
        (value("1–2", "h"), value("1.5 ± 0.5", "h", 1.5), False),
        (value("1–2", "h"), value("1", "h", 1), False),
        (value("1–2 h", "s"), value("3600–7200", "s"), False),
        (value("1–2", "h", 99), value("60–120", "min"), False),
        (value("2–1", "h"), value("60–120", "min"), False),
        (value("20 ± 1", "%", 99), value("0.2 ± 0.01", "dimensionless", 0.2), False),
        (value("20 ± -1", "%", 20), value("0.2 ± 0.01", "dimensionless", 0.2), False),
        (value("20 ± 1 SD", "%", 20), value("20 ± 1 SEM", "%", 20), False),
        (value("20 ± 1", "%", 20), value("0.2 ± 0.02", "dimensionless", 0.2), False),
        (value(">1–2", "h"), value("60–120", "min"), False),
        (value("~20 ± 1", "%", 20), value("0.2 ± 0.01", "dimensionless", 0.2), False),
        (value("1e309–2e309", "h"), value("60–120", "min"), False),
        (value("CH_{3}NH_{3}PbI_{3}"), value("CH3NH3PbI3"), True),
        (value("CH_{3}NH_{3}PbI_{3}"), value("CH3NH3PbI2"), False),
        (value("CH_{3}NH_{3}PbI_{3}"), value("MAPbI3"), False),
        (value("Co_{2}O_{3}"), value("CO2O3"), False),
        (value("I_{3-x}"), value("I3-x"), False),
        (value("x_{1} + y_{2}"), value("x1 + y2"), False),
    ],
)
def test_complete_claims(left, right, expected):
    assert equal_value(left, right, 1e-6, 0) is expected
    assert equal_value(right, left, 1e-6, 0) is expected
    if expected:
        assert _record_features(left) == _record_features(right)


def equivalence_config(truth, **overrides):
    rule = dict(
        reference_sha256=evaluate_study(truth, truth).truth_content_sha256,
        record_kind="population_statistics",
        reference_label="control",
        alternative_labels=["untreated cells"],
        reason="Synthetic fixture explicitly defines these as the same cohort.",
    )
    rule.update(overrides)
    return EvaluationConfig(descriptor_equivalences=[rule])


def test_checked_labels_preserve_original_hashes_and_do_not_credit_changed_values():
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].label = "untreated cells"
    before = evaluate_study(truth, prediction)
    config = equivalence_config(truth)
    report = evaluate_study(truth, prediction, config=config)
    assert report.core_facts.groups["population"].matched == 10
    assert report.prediction_content_sha256 == before.prediction_content_sha256
    assert report.truth_content_sha256 == before.truth_content_sha256
    assert prediction.population_statistics[0].label == "untreated cells"
    prediction.population_statistics[0].metrics[0].raw_value = "99"
    prediction.population_statistics[0].metrics[0].value_number = 99
    assert (
        evaluate_study(truth, prediction, config=config)
        .core_facts.groups["population"]
        .matched
        == 9
    )


def test_rule_cannot_follow_a_changed_reference_or_other_record_kind():
    truth = study()
    config = equivalence_config(truth)
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].label = "untreated cells"
    truth.paper.title = "A changed reference"
    assert (
        evaluate_study(truth, prediction, config=config)
        .core_facts.groups["population"]
        .matched
        == 5
    )
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].specimen_label = "untreated cells"
    assert (
        evaluate_study(truth, prediction, config=config)
        .core_facts.groups["stability"]
        .matched
        == 0
    )


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"alternative_labels": ["treated"]}, "merge distinct"),
        ({"reference_label": "missing"}, "no reference"),
    ],
)
def test_rule_cannot_merge_reference_cohorts_or_name_an_absent_cohort(
    overrides, message
):
    truth = study()
    with pytest.raises(ValueError, match=message):
        evaluate_study(truth, truth, config=equivalence_config(truth, **overrides))


def test_overlapping_rules_fail_before_scoring():
    rules = equivalence_config(study()).model_dump()["descriptor_equivalences"]
    rules.append({**rules[0], "reference_label": "treated"})
    with pytest.raises(ValidationError, match="overlap"):
        EvaluationConfig(descriptor_equivalences=rules)


def test_unlinked_specimen_equivalence_retains_its_conditions_and_checkpoint():
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].specimen_label = "untreated cells"
    config = equivalence_config(truth, record_kind="stability_tests")
    report = evaluate_study(truth, prediction, config=config)
    assert report.core_facts.groups["stability"].matched == 3
    prediction.stability_tests[0].checkpoints[0].outcomes[0].raw_value = "90"
    prediction.stability_tests[0].checkpoints[0].outcomes[0].value_number = 90
    assert (
        evaluate_study(truth, prediction, config=config)
        .core_facts.groups["stability"]
        .matched
        == 2
    )


def test_compound_and_formula_notation_changes_work_through_the_full_scorer():
    truth = study()
    truth.population_statistics[0].metrics = [
        value("20 ± 1", "%", 20),
        value("1–2", "h"),
    ]
    truth.device_families[0].full_stack_raw = "CH_{3}NH_{3}PbI_{3}"
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].metrics = [
        value("0.2 ± 0.01", "dimensionless", 0.2),
        value("60–120", "min"),
    ]
    prediction.device_families[0].full_stack_raw = "CH3NH3PbI3"
    report = evaluate_study(truth, prediction)
    assert not report.core_facts.issues
    assert report.core_facts.micro.f1 == 1


def test_nonlinear_unit_conversion_cannot_preserve_a_symmetric_uncertainty():
    # Transforming +/- 1 dB does not yield symmetric +/- (10**0.1 - 1).
    left = value("0 ± 1", "dB", 0)
    right = value("1 ± 0.2589254117941673", "dimensionless", 1)
    assert not equal_value(left, right, 1e-6, 0)


def test_literal_unit_suffix_uses_the_same_percentage_alias_as_scalar_values():
    left = value("20 ± 1 percentage", "percentage", 20)
    right = value("0.20 ± 0.01", "dimensionless", 0.2)
    assert equal_value(left, right, 1e-6, 0)


def test_unknown_semicolon_suffix_is_not_deleted():
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].label = "control; with additive"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["population"].matched == 5
    )


def test_cli_records_equivalences_and_original_input_hashes(tmp_path):
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].label = "untreated cells"
    config = equivalence_config(truth)
    for name, content in [
        ("truth", truth.model_dump(mode="json")),
        ("prediction", prediction.model_dump(mode="json")),
        ("rules", config.model_dump(mode="json")["descriptor_equivalences"]),
    ]:
        (tmp_path / f"{name}.json").write_text(json.dumps(content))
    output = tmp_path / "score.json"
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(tmp_path / "truth.json"),
            "--prediction",
            str(tmp_path / "prediction.json"),
            "--descriptor-equivalences",
            str(tmp_path / "rules.json"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    saved = json.loads(output.read_text())
    assert saved["core_facts"]["groups"]["population"]["matched"] == 10
    assert saved["config"] == config.model_dump(mode="json")
    assert (
        saved["prediction_content_sha256"]
        == evaluate_study(truth, prediction).prediction_content_sha256
    )
