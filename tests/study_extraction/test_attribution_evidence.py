"""Identity controls for sample designations and linked measurement kinds."""

import pytest

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.models import Layer, ProcessingStep
from perla_extract.study_extraction.scoring_identity import sample_code
from tests.study_extraction.test_evaluation import EVIDENCE, quantity, study


@pytest.mark.parametrize(
    "label,code",
    [
        ("Device A, Figure 4/Table 1", "A"),
        ("Device 3 measured by J-V", "3"),
        ("sample A/B", "A/B"),
        ("sample B/A", "B/A"),
        ("control cell", None),
        ("Device A and sample B", None),
    ],
)
def test_sample_designations_preserve_codes_and_exclude_unqualified_prose(label, code):
    device = study().individual_devices[0]
    device.label = label
    device.variant = None
    assert sample_code(device) == code


def named_samples():
    result = study()
    device, observation = (
        result.individual_devices[0],
        result.performance_observations[0],
    )
    device.label, device.variant = "Device A", None
    second = device.model_copy(deep=True)
    second.device_id, second.label = "second", "Device B"
    result.individual_devices.append(second)
    another = observation.model_copy(deep=True)
    another.observation_id, another.device_id = "second-result", "second"
    another.metrics = [quantity("PCE", 25, "%")]
    result.performance_observations.append(another)
    return result


def test_sample_codes_align_labels_with_extra_prose_and_do_not_follow_values():
    truth = named_samples()
    prediction = truth.model_copy(deep=True)
    for device in prediction.individual_devices:
        device.label += ", Figure 4/Table 1"
    prediction.individual_devices.reverse()
    prediction.performance_observations.reverse()
    score = evaluate_study(truth, prediction)
    assert score.core_facts.fields["measurement:pce"].f1 == 1
    assert all(
        m.identity_evidence for m in score.matches if m.kind == "individual_devices"
    )
    a, b = prediction.performance_observations
    a.metrics, b.metrics = b.metrics, a.metrics
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def test_conflicting_sample_codes_block_equal_numbers_within_same_family():
    truth, prediction = named_samples(), named_samples()
    prediction.individual_devices = prediction.individual_devices[:1]
    prediction.individual_devices[0].label = "Device C"
    prediction.performance_observations = prediction.performance_observations[:1]
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def measurement_siblings(method):
    truth = study()
    prediction = truth.model_copy(deep=True)
    second = prediction.individual_devices[0].model_copy(deep=True)
    second.device_id = "other-specimen"
    prediction.individual_devices.append(second)
    another = prediction.performance_observations[0].model_copy(deep=True)
    another.observation_id, another.device_id = "other-result", second.device_id
    another.measurement_type = method
    another.metrics = [quantity("PCE", 25, "%")]
    prediction.performance_observations.append(another)
    return truth, prediction


def test_measurement_role_distinguishes_jv_from_eqe_specimen_without_using_values():
    truth, prediction = measurement_siblings("eqe_integrated_current")
    result = evaluate_study(truth, prediction)
    assert result.core_facts.fields["measurement:pce"].matched == 1
    pair = next(m for m in result.matches if m.kind == "individual_devices")
    assert pair.identity_evidence["prediction_measurement_types"] == ["jv_scan"]
    prediction.performance_observations[0].metrics = [quantity("PCE", 10, "%")]
    prediction.performance_observations[1].metrics = [quantity("PCE", 20, "%")]
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def test_same_measurement_siblings_stay_ambiguous_despite_one_equal_pce():
    truth, prediction = measurement_siblings("jv_scan")
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def test_illumination_and_voltage_interval_aliases_preserve_explicit_requirements():
    truth, prediction = study(), study()
    first = quantity("illumination", None, None)
    first.raw_value = "AM 1.5G irradiation"
    second = quantity("illumination spectrum", None, None)
    second.raw_value = "AM1.5G"
    truth.performance_observations[0].metrics.extend(
        [
            first,
            quantity("scan protocol", None, "V"),
        ]
    )
    truth.performance_observations[0].metrics[-1].raw_value = "1.2 to -0.05 V"
    prediction.performance_observations[0].measurement_conditions = [
        second,
        quantity("voltage scan protocol", None, "V"),
    ]
    prediction.performance_observations[0].measurement_conditions[
        -1
    ].raw_value = "1.2 to -0.05 V"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 1
    )
    prediction.performance_observations[0].measurement_conditions[
        -1
    ].raw_value = "0.05 to 1.2 V"
    assert (
        evaluate_study(truth, prediction).core_facts.fields["measurement:pce"].matched
        == 0
    )


def test_active_area_and_layer_material_have_independent_field_metrics():
    truth = study()
    truth.individual_devices[0].reported_properties = [
        quantity("active area", 0.2, "cm2")
    ]
    truth.device_families[0].layers = [
        Layer(
            layer_id="active",
            sequence=1,
            role="absorber",
            material="MAPbI3",
            reported_properties=[quantity("bandgap", 1.6, "eV")],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.individual_devices[0].reported_properties = [
        quantity("active area", 20, "mm2")
    ]
    prediction.device_families[0].layers[0].reported_properties = [
        quantity("bandgap", 1.8, "eV")
    ]
    fields = evaluate_study(truth, prediction).core_facts.fields
    assert fields["processing:activearea"].f1 == 1
    assert fields["material:material"].f1 == 1
    assert fields["material:bandgap"].matched == 0


def test_process_temperature_and_duration_keep_independent_counts_at_same_step():
    truth = study()
    truth.device_families[0].processing_steps = [
        ProcessingStep(
            step_id="anneal",
            sequence=1,
            operation="annealing",
            target_layer_ids=[],
            materials=[],
            conditions=[
                quantity("annealing temperature", 100, "degC"),
                quantity("annealing duration", 10, "min"),
            ],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps[0].conditions = [
        quantity("temperature", 373.15, "K"),
        quantity("duration", 20, "min"),
    ]
    fields = evaluate_study(truth, prediction).core_facts.fields
    assert fields["processing:temperature"].f1 == 1
    assert fields["processing:duration"].matched == 0
    assert fields["processing:operation"].f1 == 1
