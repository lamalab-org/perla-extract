"""Photovoltaic outcomes belong to a measurement protocol, not just a device."""

import pytest
from test_scoring_contracts import study as cohort_study

from perla_extract.study_extraction.evaluation import evaluate_study
from tests.study_extraction.test_evaluation import quantity, study


def illuminated_measurement():
    result = study()
    result.performance_observations[0].metrics.append(
        quantity("illumination intensity", 100, "mW/cm2")
    )
    return result


@pytest.mark.parametrize("illumination", [50, None])
def test_conflicting_or_missing_illumination_cannot_earn_strict_pce_credit(
    illumination,
):
    truth = illuminated_measurement()
    prediction = truth.model_copy(deep=True)
    metrics = prediction.performance_observations[0].metrics
    if illumination is None:
        metrics.pop()
    else:
        metrics[1] = quantity("illumination intensity", illumination, "mW/cm2")
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 0
    assert score.value_only.groups["performance"].matched == 1


def test_equivalent_irradiance_units_preserve_outcome_and_protocol_credit():
    truth = illuminated_measurement()
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics[1] = quantity(
        "irradiance", 1000, "W/m2"
    )
    assert evaluate_study(truth, prediction).core_facts.groups["performance"].f1 == 1


def test_protocols_disambiguate_observations_without_using_their_outcomes():
    truth = illuminated_measurement()
    second = truth.performance_observations[0].model_copy(deep=True)
    second.observation_id = "dim-light"
    second.metrics = [quantity("PCE", 10, "%"), quantity("irradiance", 50, "mW/cm2")]
    truth.performance_observations.append(second)
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations.reverse()
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 4
    for i, number in enumerate([20, 10]):
        prediction.performance_observations[i].metrics[0] = quantity("PCE", number, "%")
    score = evaluate_study(truth, prediction).core_facts
    assert (
        score.groups["performance"].matched == 2
    )  # Only the conditions remain correct.


@pytest.mark.parametrize(
    "protocol,credit", [("jv_scan", 1), ("eqe_integrated_current", 0)]
)
def test_eta_alias_is_scoped_to_power_efficiency(protocol, credit):
    truth = study()
    truth.performance_observations[0].measurement_type = protocol
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics[0].name = "η"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["performance"].matched
        == credit
    )


def test_missing_cohort_size_loses_only_the_count_when_identity_is_unambiguous():
    truth = cohort_study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].sample_size = None
    score = evaluate_study(truth, prediction).core_facts.groups["population"]
    assert (score.matched, score.predicted, score.truth) == (9, 9, 10)


def test_conflicting_cohort_sizes_still_withhold_affected_outcome_credit():
    truth = cohort_study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].sample_size = 21
    assert (
        evaluate_study(truth, prediction).core_facts.groups["population"].matched == 5
    )


def test_rounding_of_many_conditions_does_not_prevent_record_pairing():
    truth = illuminated_measurement()
    truth.performance_observations[0].metrics.extend(
        [
            quantity("scan rate", 100, "mV/s"),
            quantity("measurement temperature", 300, "K"),
            quantity("voltage step", 10, "mV"),
        ]
    )
    prediction = truth.model_copy(deep=True)
    for value in prediction.performance_observations[0].metrics[1:]:
        value.value_number *= 1.005
        value.raw_value = str(value.value_number)
    assert evaluate_study(truth, prediction).core_facts.groups["performance"].f1 == 1


def test_protocol_omission_does_not_prove_two_observations_are_distinct():
    truth = illuminated_measurement()
    second = truth.performance_observations[0].model_copy(deep=True)
    second.observation_id = "incompletely-described"
    second.metrics.pop()
    truth.performance_observations.append(second)
    score = evaluate_study(truth, truth).core_facts
    assert score.groups["performance"].matched == 0
    assert score.scoring_status == "needs_review"
