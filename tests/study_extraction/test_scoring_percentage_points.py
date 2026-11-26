"""A fixed percentage scale is symmetric, explicit and independent of value size."""

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.evaluation_cli import main
from tests.study_extraction.test_attribution_missingness import cohort
from tests.study_extraction.test_evaluation import quantity, scientific_study, study


@pytest.mark.parametrize("name,number", [("PCE", 20), ("FF", 80)])
@pytest.mark.parametrize("unit", [None, "", "   "])
def test_percentage_points_are_applied_independently_on_either_side(name, number, unit):
    explicit, implicit = study(), study()
    explicit.performance_observations[0].metrics = [quantity(name, number, "%")]
    implicit.performance_observations[0].metrics = [quantity(name, number, unit)]
    originals = explicit.model_dump_json(), implicit.model_dump_json()
    for truth, prediction, side in (
        (explicit, implicit, "prediction"),
        (implicit, explicit, "truth"),
    ):
        report = evaluate_study(truth, prediction).core_facts
        assert report.fields["measurement:" + name.lower()].f1 == 1
        fact = next(
            d for d in report.diagnostics.facts[side] if d.path.endswith("/metrics/0")
        )
        assert fact.unit_source == "percentage_points"
    assert (explicit.model_dump_json(), implicit.model_dump_json()) == originals


@pytest.mark.parametrize(
    "raw,number,unit,expected,source,matched",
    [
        ("0.2", 0.2, None, 20, "percentage_points", 0),
        ("0.2", 0.2, None, 0.2, "percentage_points", 1),
        ("0.2", 0.2, "dimensionless", 20, "reported", 1),
        ("0.2 dimensionless", None, None, 20, "raw_value", 1),
        ("0.2 dimensionless", None, "", 20, "raw_value", 1),
        ("20%", None, None, 20, "raw_value", 1),
        ("20", None, None, 20, "percentage_points", 1),
        ("20 V", None, None, 20, "raw_value", 0),
        ("20 unknownunit", None, None, 20, "unparsed", 0),
        ("20", 25, None, 20, "unparsed", 0),
        ("mean 20", 20, None, 20, "unparsed", 0),
        ("<20", 20, None, 20, "unparsed", 0),
    ],
)
def test_explicit_units_and_full_claims_precede_the_convention(
    raw, number, unit, expected, source, matched
):
    truth, prediction = study(), study()
    truth.performance_observations[0].metrics = [quantity("PCE", expected, "%")]
    value = quantity("PCE", number, unit)
    value.raw_value = raw
    prediction.performance_observations[0].metrics = [value]
    core = evaluate_study(truth, prediction).core_facts
    assert core.fields["measurement:pce"].matched == matched
    assert core.fields["measurement:pce"].predicted == 1
    fact = next(
        d for d in core.diagnostics.facts["prediction"] if d.path.endswith("/metrics/0")
    )
    assert fact.unit_source == source


@pytest.mark.parametrize("raw", ["20 ± 1", "19 to 21"])
def test_compound_quantities_retain_the_complete_claim(raw):
    truth, prediction = study(), study()
    for result, unit in ((truth, "%"), (prediction, None)):
        value = quantity("PCE", None, unit)
        value.raw_value = raw
        result.performance_observations[0].metrics = [value]
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].f1 == 1
    )
    prediction.performance_observations[0].metrics = [quantity("PCE", 20, None)]
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


@pytest.mark.parametrize("name,number,unit", [("Voc", 1.1, "V"), ("Jsc", 22, "mA/cm2")])
def test_other_quantities_do_not_acquire_percentage_units(name, number, unit):
    truth, prediction = study(), study()
    truth.performance_observations[0].metrics = [quantity(name, number, unit)]
    prediction.performance_observations[0].metrics = [quantity(name, number, None)]
    core = evaluate_study(truth, prediction).core_facts
    assert core.fields["measurement:" + name.lower()].matched == 0
    assert not any(
        d.unit_source == "percentage_points"
        for d in core.diagnostics.facts["prediction"]
    )


def test_population_means_and_absolute_aging_pce_use_the_same_convention():
    truth = cohort()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].metrics[0].unit = None
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].f1 == 1
    )
    truth = scientific_study()
    truth.stability_tests[0].checkpoints[0].outcomes = [quantity("PCE", 20, "%")]
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].checkpoints[0].outcomes[0].unit = None
    assert (
        evaluate_study(truth, prediction).core_facts.fields["aging_outcome:pce"].f1 == 1
    )
    prediction.stability_tests[0].checkpoints[0].outcomes[0].name = "retained PCE"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["aging_outcome:pce"].matched
        == 0
    )


def test_cli_keeps_missing_voltage_units_visible(tmp_path):
    truth, prediction = study(), study()
    for name, result, unit in (("truth", truth, None), ("prediction", prediction, "V")):
        result.performance_observations[0].metrics = [quantity("Voc", 1.1, unit)]
        (tmp_path / f"{name}.json").write_text(result.model_dump_json())
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(tmp_path / "truth.json"),
            "--prediction",
            str(tmp_path / "prediction.json"),
            "--output",
            str(tmp_path / "evaluation.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "PV unit evidence missing: 1 reference, 0 predicted" in result.output
