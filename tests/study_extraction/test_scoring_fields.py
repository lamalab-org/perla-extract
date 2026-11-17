"""Field credit must follow scientific meaning without changing the reference."""

import pytest
from pydantic import ValidationError

from perla_extract.study_extraction.evaluation import (
    EvaluationReport,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.models import PopulationStatistic
from tests.study_extraction.test_evaluation import EVIDENCE, provenance, quantity, study


def pv_fields():
    result = study()
    result.performance_observations[0].metrics = [
        quantity("PCE", 20, "%"),
        quantity("FF", 80, "%"),
        quantity("Voc", 1.1, "V"),
        quantity("Jsc", 22, "mA/cm2"),
    ]
    return result


def test_one_wrong_value_does_not_remove_other_field_credit():
    truth = pv_fields()
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics[1] = quantity("FF", 70, "%")
    fields = evaluate_study(truth, prediction).core_facts.fields
    assert fields["measurement:ff"].matched == 0
    assert fields["measurement:ff"].truth == fields["measurement:ff"].predicted == 1
    for name in ("pce", "voc", "jsc"):
        assert fields["measurement:" + name].f1 == 1


def test_known_family_supports_pairing_without_requiring_identical_display_prose():
    truth, prediction = study(), study(prefix="prediction")
    truth.individual_devices[0].champion_status = "not_reported"
    truth.individual_devices[0].selection_basis = "other"
    prediction.individual_devices[0].champion_status = "not_reported"
    prediction.individual_devices[0].selection_basis = "other"
    prediction.individual_devices[0].label = "Device shown in Table 1"
    prediction.individual_devices[0].variant = "untreated reference specimen"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].f1 == 1
    )
    missing_truth = truth.model_copy(deep=True)
    missing_prediction = prediction.model_copy(deep=True)
    missing_truth.individual_devices[0].family_id = None
    missing_prediction.individual_devices[0].family_id = None
    assert (
        evaluate_study(missing_truth, missing_prediction)
        .core_facts.fields["measurement:pce"]
        .matched
        == 0
    )
    # A second equally plausible sibling cannot be resolved by its PCE value.
    sibling = prediction.individual_devices[0].model_copy(deep=True)
    sibling.device_id = "sibling"
    prediction.individual_devices.append(sibling)
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


@pytest.mark.parametrize(
    "name",
    [
        "PCE extracted from the J-V curve",
        "PCE extracted from J–V curve",
        "power conversion efficiency",
    ],
)
def test_pv_name_equivalence_preserves_method(name):
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics[0].name = name
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].f1 == 1
    )
    prediction.performance_observations[0].measurement_type = "stabilized_power_output"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


@pytest.mark.parametrize(
    "name", ["relative PCE improvement", "normalized PCE", "PCE retention"]
)
def test_qualifiers_are_not_stripped_to_raise_absolute_pce_credit(name):
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics[0].name = name
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def test_population_mean_names_match_but_different_statistics_do_not():
    truth = study()
    truth.performance_observations = []
    truth.population_statistics = [
        PopulationStatistic(
            population_id="cohort",
            family_id=truth.device_families[0].family_id,
            label="control",
            statistic_type="mean",
            sample_size=20,
            measurement_type="jv_scan",
            scan_direction="reverse",
            metrics=[quantity("population-average PCE", 18, "%")],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].metrics[0].name = "average PCE"
    score = evaluate_study(truth, prediction).core_facts
    assert score.fields["measurement:pce"].f1 == 1
    assert score.strata["measurement:pce|population|mean|jv_scan"].f1 == 1
    prediction.population_statistics[0].statistic_type = "maximum"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def test_extra_protocol_is_not_a_pv_error_but_missing_known_protocol_is():
    truth, prediction = study(), study()
    prediction.performance_observations[0].measurement_conditions = [
        quantity("irradiance", 100, "mW/cm2")
    ]
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].f1 == 1
    )
    assert (
        evaluate_study(prediction, truth).core_facts.fields["measurement:pce"].matched
        == 0
    )


@pytest.mark.parametrize(
    "spectrum,matched", [("AM 1.5G irradiation", 1), ("AM1.5D", 0)]
)
def test_explicit_spectrum_notation_is_normalized_without_merging_different_spectra(
    spectrum, matched
):
    truth, prediction = study(), study()
    truth.performance_observations[0].measurement_conditions = [
        quantity("illumination spectrum", None, None)
    ]
    truth.performance_observations[0].measurement_conditions[0].raw_value = spectrum
    prediction.performance_observations[0].measurement_conditions = [
        quantity("illumination spectrum", None, None)
    ]
    prediction.performance_observations[0].measurement_conditions[
        0
    ].raw_value = "AM 1.5G"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == matched
    )


def test_duplicates_missing_values_and_nulls_have_honest_denominators():
    truth, prediction = pv_fields(), pv_fields()
    prediction.performance_observations[0].metrics.pop()  # missing Jsc
    prediction.performance_observations[0].metrics.append(quantity("PCE", 20, "%"))
    fields = evaluate_study(truth, prediction).core_facts.fields
    assert fields["measurement:pce"].precision == 0.5
    assert fields["measurement:jsc"].recall == 0
    assert fields["measurement:jsc"].precision is None


def test_field_micro_aggregation_uses_counts_and_report_validation_checks_ledger():
    first = evaluate_study(pv_fields(), pv_fields(), benchmark=provenance("a"))
    prediction = pv_fields()
    prediction.performance_observations[0].metrics.append(quantity("PCE", 20, "%"))
    second_truth = pv_fields()
    second_truth.paper.doi = prediction.paper.doi = "10.1/second"
    second = evaluate_study(second_truth, prediction, benchmark=provenance("b"))
    combined = aggregate_evaluations([first, second], bootstrap_samples=0)
    assert combined.core_fields_micro["measurement:pce"].precision == pytest.approx(
        2 / 3
    )
    payload = first.model_dump()
    payload["core_facts"]["fields"]["measurement:pce"] = payload["core_facts"][
        "fields"
    ]["measurement:ff"].copy()
    payload["core_facts"]["fields"].pop("measurement:ff")
    with pytest.raises(ValidationError, match="field counts"):
        EvaluationReport.model_validate(payload)
