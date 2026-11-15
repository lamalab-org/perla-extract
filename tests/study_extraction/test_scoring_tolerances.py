"""One-percent scoring permits rounded values without erasing units or scope."""

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation import (
    EvaluationConfig,
    EvaluationReport,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_cli import main
from perla_extract.study_extraction.models import PopulationStatistic, ReportedValue
from tests.study_extraction.test_evaluation import EVIDENCE, provenance, study, value


def claim(raw):
    return ReportedValue(
        name="metric", raw_value=raw, value_number=None, unit=None, evidence=EVIDENCE
    )


@pytest.mark.parametrize(
    "reference,prediction,matched",
    [
        ("20.0 %", "20.04 %", 1),
        ("20 %", "20.20 %", 1),
        ("20 %", "20.21 %", 0),
        ("20 %", "0.201 dimensionless", 1),
        ("20 %", "20", 0),
        ("1.00 V", "1009 mV", 1),
        ("1.00 V", "1011 mV", 0),
        ("20 mA/cm2", "201.5 A/m2", 1),
        ("20 mA/cm2", "203 A/m2", 0),
        ("125 nm", "0.126 µm", 1),
        ("125 nm", "0.127 µm", 0),
        ("60 min", "3630 s", 1),
        ("60 min", "3640 s", 0),
        ("65 °C", "67 °C", 1),  # Relative temperature error uses kelvin.
        ("65 °C", "69 °C", 0),
        ("0 nm", "0.000001 nm", 0),
        ("-100 mV", "-100.5 mV", 1),
        ("-100 mV", "100 mV", 0),
        ("20–30 %", "20.1–30.2 %", 1),
        ("20–30 %", "20.1–30.4 %", 0),
        ("20 ± 1 %", "20.1 ± 1.005 %", 1),
        ("20 ± 1 %", "20.1 ± 1.02 %", 0),
        (">20 %", "20.1 %", 0),
    ],
)
def test_public_fact_credit_at_one_percent(reference, prediction, matched):
    for a, b in ((reference, prediction), (prediction, reference)):
        result = evaluate_study(study(pce=claim(a)), study(pce=claim(b)))
        assert result.core_facts.groups["performance"].matched == matched
        assert result.config.numeric_relative_tolerance == 0.01
        assert result.config.numeric_absolute_tolerance == 0


def test_sample_sizes_remain_exact_at_one_percent():
    truth = study()
    truth.population_statistics = [
        PopulationStatistic(
            population_id="cohort",
            family_id=truth.device_families[0].family_id,
            label="control",
            statistic_type="mean",
            sample_size=100,
            metrics=[value()],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].sample_size = 101
    result = evaluate_study(truth, prediction)
    assert result.core_facts.subfields["statistical_metadata"].matched == 0
    assert not any(m.truth_path.startswith("/population_statistics/") for m in result.core_facts.matches)
    assert result.core_facts.recovery.groups["performance"].matched == 2  # Individual and population values, never the count.


def test_cli_default_and_strict_override_are_frozen_in_reports(tmp_path):
    truth, prediction = tmp_path / "truth.json", tmp_path / "prediction.json"
    truth.write_text(study().model_dump_json(), encoding="utf-8")
    prediction.write_text(
        study(pce=value("20.04%", 20.04)).model_dump_json(), encoding="utf-8"
    )
    for options, tolerance, matched, name in [
        ([], 0.01, 1, "default"),
        (["--numeric-relative-tolerance", "1e-6"], 1e-6, 0, "strict"),
    ]:
        output = tmp_path / f"{name}.json"
        result = CliRunner().invoke(
            main,
            [
                "--truth",
                str(truth),
                "--prediction",
                str(prediction),
                "--output",
                str(output),
                *options,
            ],
        )
        assert result.exit_code == 0, result.output
        score = EvaluationReport.model_validate_json(output.read_bytes())
        assert score.config.numeric_relative_tolerance == tolerance
        assert score.core_facts.groups["performance"].matched == matched


def test_aggregation_refuses_to_mix_strict_and_one_percent_scores():
    scores = [
        evaluate_study(study(), study(), benchmark=provenance(str(i)), config=config)
        for i, config in enumerate(
            [EvaluationConfig(), EvaluationConfig(numeric_relative_tolerance=1e-6)]
        )
    ]
    with pytest.raises(ValueError, match="incompatible"):
        aggregate_evaluations(scores, bootstrap_samples=0)
