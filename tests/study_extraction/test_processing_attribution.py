"""Representation changes may restore credit; scientific contradictions may not."""

import pytest

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.models import Layer, ProcessingStep
from perla_extract.study_extraction.scoring_vocabulary import processing_name
from tests.study_extraction.test_evaluation import EVIDENCE, quantity, study


@pytest.mark.parametrize("separator", [" ", "_", "-"])
def test_processing_duration_alias_retains_operation_and_ordinal(separator):
    truth = study()
    truth.device_families[0].processing_steps = [
        ProcessingStep(
            step_id="recipe",
            sequence=1,
            operation="Prepare precursor, spin-coat and anneal",
            target_layer_ids=[],
            materials=[],
            conditions=[quantity("first spin duration", 30, "s")],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    condition = prediction.device_families[0].processing_steps[0].conditions[0]
    condition.name = separator.join(["first", "spin", "time"])
    condition.raw_value, condition.value_number, condition.unit = "0.5 min", 0.5, "min"
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["processing"].f1 == 1
    condition.name = "second spin time"
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["processing"].matched == 1  # Operation only.
    assert score.recovery.groups["processing"].matched == 1


def test_duration_alias_does_not_change_measurements_or_unfamiliar_words():
    assert processing_name("lifetime") != processing_name("life duration")
    truth = study()
    truth.performance_observations[0].metrics = [quantity("response time", 30, "s")]
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics[0].name = "response duration"
    assert (
        evaluate_study(truth, prediction)
        .core_facts.recovery.groups["performance"]
        .matched
        == 0
    )


def designs():
    truth = study()
    family = truth.device_families[0]
    family.layers = [
        Layer(
            layer_id="electrode",
            sequence=1,
            role="transparent_electrode",
            material="ITO",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    ]
    family.label, family.variant = "Control inverted design", "Control"
    other = family.model_copy(deep=True)
    other.family_id, other.label, other.variant = (
        "modified",
        "Additive-modified inverted design",
        "Target",
    )
    truth.device_families.append(other)
    device = truth.individual_devices[0].model_copy(deep=True)
    device.device_id, device.family_id, device.label = (
        "modified-device",
        "modified",
        "modified champion",
    )
    truth.individual_devices.append(device)
    observation = truth.performance_observations[0].model_copy(deep=True)
    observation.observation_id, observation.device_id = (
        "modified-result",
        device.device_id,
    )
    truth.performance_observations.append(observation)
    prediction = truth.model_copy(deep=True)
    for family, label in zip(
        prediction.device_families,
        ["Inverted control design", "Inverted additive-modified design"],
    ):
        family.label, family.variant = label, None
    prediction.device_families.reverse()
    return truth, prediction


def owners(report):
    return {
        (m.kind, m.truth_id, m.predicted_id, m.ambiguous)
        for m in report.matches
        if m.kind in {"device_families", "individual_devices"}
    }


def test_family_labels_resolve_equal_stack_candidates_without_using_results():
    truth, prediction = designs()
    report = evaluate_study(truth, prediction)
    assert report.core_facts.groups["performance"].matched == 2
    assert all(not m.ambiguous for m in report.matches if m.kind == "device_families")
    prediction.performance_observations[0].metrics[0] = quantity("PCE", 99, "%")
    prediction.performance_observations[1].metrics[0] = quantity("PCE", 98, "%")
    changed = evaluate_study(truth, prediction)
    assert owners(changed) == owners(report)
    assert changed.core_facts.groups["performance"].matched == 0
    for family in prediction.device_families:
        family.label = "Inverted design"
    ambiguous = evaluate_study(truth, prediction)
    assert all(m.ambiguous for m in ambiguous.matches if m.kind == "device_families")
    assert ambiguous.core_facts.groups["performance"].matched == 0


def test_device_label_words_preserve_treatment_arm_and_missing_protocol():
    truth = study()
    device = truth.individual_devices[0]
    device.variant, device.label = None, "TiCl4 processing: 5 min"
    other = device.model_copy(deep=True)
    other.device_id, other.label = "long", "TiCl4 processing: 10 min"
    truth.individual_devices.append(other)
    observation = truth.performance_observations[0].model_copy(deep=True)
    observation.observation_id, observation.device_id = "long-result", "long"
    truth.performance_observations.append(observation)
    prediction = truth.model_copy(deep=True)
    prediction.individual_devices[0].label = "5 min processing with TiCl4"
    prediction.individual_devices[1].label = "10 min processing with TiCl4"
    prediction.individual_devices.reverse()
    report = evaluate_study(truth, prediction)
    assert report.core_facts.groups["performance"].matched == 2
    assert all(
        not m.ambiguous for m in report.matches if m.kind == "individual_devices"
    )
    prediction.performance_observations[0].scan_direction = "not_reported"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["performance"].matched == 1
    )
    for device in prediction.individual_devices:
        device.label = "TiCl4 processing"
    ambiguous = evaluate_study(truth, prediction)
    assert ambiguous.core_facts.groups["performance"].matched == 0
    assert any(m.ambiguous for m in ambiguous.matches if m.kind == "individual_devices")


def test_explicit_sample_code_conflict_overrides_label_overlap():
    truth = study()
    truth.individual_devices[0].label = "Device A, champion control"
    prediction = truth.model_copy(deep=True)
    prediction.individual_devices[0].label = "Device B, champion control"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["performance"].matched == 0
    )


def test_generic_family_heading_cannot_resolve_otherwise_tied_designs():
    truth, prediction = designs()
    for family, label in zip(
        truth.device_families,
        ["Laboratory double-cation PSC", "High-performance double-cation PSC"],
    ):
        family.label, family.variant = label, None
    for family, label in zip(
        prediction.device_families, ["Double-cation PSC small", "Double-cation module"]
    ):
        family.label, family.variant = label, None
    report = evaluate_study(truth, prediction)
    assert all(m.ambiguous for m in report.matches if m.kind == "device_families")
    assert report.core_facts.groups["performance"].matched == 0


def test_parent_priority_cannot_bypass_the_final_specimen_similarity_cutoff():
    truth = study()
    truth.individual_devices[
        0
    ].label = "Prepared specimen for electrical characterization"
    prediction = truth.model_copy(deep=True)
    prediction.individual_devices[
        0
    ].label = "Solar cell recorded during photovoltaic scan"
    prediction.performance_observations[0].measurement_type = "eqe_integrated_current"
    report = evaluate_study(truth, prediction)
    assert not any(m.kind == "individual_devices" for m in report.matches)


def test_same_explicit_device_code_supports_paraphrase_with_missing_method_metadata():
    truth = study()
    truth.individual_devices[0].label = "Device 4 used for MPP tracking"
    truth.individual_devices[0].reported_properties = [
        quantity("masked active area", 9, "mm2")
    ]
    truth.performance_observations.clear()
    prediction = truth.model_copy(deep=True)
    prediction.individual_devices[
        0
    ].label = "Device 4, Figure 2b continuous MPP-output specimen"
    assert (
        evaluate_study(truth, prediction)
        .core_facts.groups["material_device_identity"]
        .f1
        == 1
    )


def test_ambiguous_family_assignment_cannot_choose_child_partners_by_array_order():
    truth, prediction = designs()
    for record in [*truth.device_families, *prediction.device_families]:
        record.label, record.variant = "Generic design", None
    before = evaluate_study(truth, prediction)
    prediction.device_families.reverse()
    after = evaluate_study(truth, prediction)

    def devices(report):
        return {
            (m.truth_id, m.predicted_id, m.ambiguous)
            for m in report.matches
            if m.kind == "individual_devices"
        }

    assert devices(before) == devices(after)
    assert (
        before.core_facts.groups["performance"].matched
        == after.core_facts.groups["performance"].matched
        == 0
    )
