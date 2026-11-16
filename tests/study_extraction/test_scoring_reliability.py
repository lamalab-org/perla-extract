"""Real representation gaps paired with deliberately wrong scientific neighbors.

These are assistant-inspected regression controls, not independent validation.
The fixture records its saved-prediction hashes and selection; references remain
in the existing release. No aggregate model score is a test expectation.
"""

import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.evaluation import (
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.models import (
    DeviceFamily,
    IndividualDevice,
    PerformanceObservation,
    ProcessingStep,
    StudyExtraction,
    SubjectContext,
)
from perla_extract.study_extraction.scoring_explanation import FactSelection
from perla_extract.study_extraction.scoring_facts import scientific_facts
from perla_extract.study_extraction.scoring_identity import record_similarity
from perla_extract.study_extraction.scoring_vocabulary import (
    material_name,
    property_name,
)
from tests.study_extraction.test_evaluation import study, value

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "tests/fixtures/scoring/reliability_cases.json").read_text()
)["cases"]
METRIC = "/performance_observations/0/metrics/0"


def champion():
    case = CASES[-1]
    truth, prediction = study(), study(prefix="pred")
    for target, prefix in ((truth, "reference"), (prediction, "prediction")):
        target.individual_devices = [
            IndividualDevice.model_validate(case[f"{prefix}_device"])
        ]
        target.performance_observations = [
            PerformanceObservation.model_validate(case[f"{prefix}_observation"])
        ]
        target.individual_devices[0].family_id = target.device_families[0].family_id
    return truth, prediction


def credited(score, path=METRIC):
    return any(m.truth_path == path for m in score.core_facts.matches)


def test_source_backed_champion_treatment_representations_agree():
    truth, prediction = champion()
    result, explanation = evaluate_study_with_explanations(
        truth, prediction, facts=[FactSelection(side="truth", path=METRIC)]
    )
    assert credited(result)
    assert explanation.facts[0].candidates[0].context_status == "equivalent"
    assert result == evaluate_study(truth, prediction)
    # Representation equivalence is symmetric, independent of which side is truth.
    assert credited(evaluate_study(prediction, truth))


@pytest.mark.parametrize(
    "change",
    [
        "treatment",
        "missing_input",
        "stage",
        "material",
        "unknown_condition",
        "scan",
        "owner",
        "value",
    ],
)
def test_champion_near_neighbors_do_not_receive_attributed_credit(change):
    truth, prediction = champion()
    device = prediction.individual_devices[0]
    if change == "treatment":
        device.subject_context.conditions[-1].value_number = 100
        device.subject_context.conditions[-1].raw_value = "100 s"
    elif change == "missing_input":
        device.subject_context.conditions.pop()
    elif change == "stage":
        device.subject_context.treatment_stage = "completed_device"
    elif change == "material":
        device.subject_context.treated_material = "Ag"
    elif change == "unknown_condition":
        device.subject_context.conditions.append(
            value("different additive", None, None).model_copy(
                update={"name": "unparsed input"}
            )
        )
    elif change == "scan":
        prediction.performance_observations[0].scan_direction = "forward"
    elif change == "owner":
        prediction.performance_observations[0].device_id = None
    else:
        prediction.performance_observations[0].metrics[0].value_number = 15.9
        prediction.performance_observations[0].metrics[0].raw_value = "15.9%"
    result = evaluate_study(truth, prediction)
    assert not credited(result)
    if change != "value":
        assert any(m.truth_path == METRIC for m in result.core_facts.recovery.matches)


def test_unknown_subject_context_is_required_but_cannot_disambiguate_identity():
    truth, prediction = study(), study()
    prediction.individual_devices[0].subject_context = SubjectContext(
        treatment_stage="completed_device",
        treated_material="Au",
        conditions=[value("unknown", None, None)],
    )
    assert not credited(evaluate_study(truth, prediction))


def test_duplicate_treatment_views_preserve_inventory_and_original_paths():
    truth, prediction = champion()
    before = evaluate_study(prediction, prediction)
    device = prediction.individual_devices[0]
    device.reported_properties.extend(
        v.model_copy(deep=True) for v in device.subject_context.conditions
    )
    after = evaluate_study(prediction, prediction)
    assert after.core_facts.micro == before.core_facts.micro
    assert any(
        len(paths) == 2 for paths in after.core_facts.fact_sources["truth"].values()
    )
    device.reported_properties.append(
        device.reported_properties[-1].model_copy(deep=True)
    )
    duplicated = scientific_facts(prediction, {}, "truth", {}).facts
    assert len(duplicated) == after.core_facts.micro.truth + 1


@pytest.mark.parametrize("index", [0, 1])
def test_source_backed_structural_cases_ignore_prose_and_absolute_layer_indices(index):
    case = CASES[index]
    truth = StudyExtraction.model_validate_json(
        (
            ROOT
            / "data/study_extraction/ground_truth/v2/dev"
            / case["paper"]
            / "ground_truth.json"
        ).read_bytes()
    )
    prediction = truth.model_copy(deep=True)
    prediction.device_families = [
        DeviceFamily.model_validate(f) for f in case["prediction_families"]
    ]
    a, b = truth.device_families[0], prediction.device_families[0]
    assert record_similarity(a, b, 0.01, 0) > 0.35
    # Test layer facts alone: downstream records still use their original IDs.
    truth.individual_devices = prediction.individual_devices = []
    truth.performance_observations = prediction.performance_observations = []
    truth.population_statistics = prediction.population_statistics = []
    truth.stability_tests = prediction.stability_tests = []
    result = evaluate_study(truth, prediction)
    matches = {m.predicted_path for m in result.core_facts.matches}
    assert all(
        f"/device_families/0/layers/{i}/material" in matches
        for i in range(len(b.layers))
    )
    if index == 0:
        assert (
            "/device_families/0/layers/0/material"
            in result.core_facts.unmatched_truth_paths
        )


def test_layer_renumbering_does_not_change_recipe_target_identity():
    case = CASES[0]
    a = DeviceFamily.model_validate(case["prediction_families"][0])
    truth = study()
    truth.device_families = [a]
    truth.individual_devices[0].family_id = a.family_id
    a.processing_steps = [
        ProcessingStep(
            step_id="anneal",
            sequence=1,
            operation="annealing",
            target_layer_ids=[a.layers[2].layer_id],
            materials=[],
            conditions=[
                value("100 C", 100, "C").model_copy(update={"name": "temperature"})
            ],
            evidence=a.evidence,
        )
    ]
    prediction = truth.model_copy(deep=True)
    for i, layer in enumerate(prediction.device_families[0].layers):
        layer.sequence = i + 10
    result = evaluate_study(truth, prediction)
    assert result.core_facts.micro.f1 == 1
    prediction.device_families[0].layers[2].material = "CH3NH3PbI2Cl"
    result = evaluate_study(truth, prediction)
    assert (
        "/device_families/0/layers/2/material"
        in result.core_facts.unmatched_truth_paths
    )
    assert (
        "/device_families/0/layers/2/material"
        in result.core_facts.unmatched_prediction_paths
    )


def test_layer_order_and_chemical_case_remain_meaningful():
    a = DeviceFamily.model_validate(CASES[0]["prediction_families"][0])
    b = a.model_copy(deep=True)
    b.layers[0].sequence, b.layers[-1].sequence = (
        b.layers[-1].sequence,
        b.layers[0].sequence,
    )
    assert record_similarity(a, b, 0.01, 0) < 1
    assert material_name("Co2O3") != material_name("CO2O3")
    assert material_name("I-SnO2") != material_name("SnO2")
    assert material_name("doped TiO2") != material_name("TiO2")
    assert material_name("compact TiO2") != material_name("mesoporous TiO2")
    assert material_name("Gold (Ag, vendor)") != "Au"


def test_collapsed_families_are_not_silently_repaired():
    case = CASES[3]
    truth = StudyExtraction.model_validate_json(
        (
            ROOT
            / "data/study_extraction/ground_truth/v2/dev"
            / case["paper"]
            / "ground_truth.json"
        ).read_bytes()
    )
    prediction = truth.model_copy(deep=True)
    prediction.device_families = [
        DeviceFamily.model_validate(f) for f in case["prediction_families"]
    ]
    result = evaluate_study(truth, prediction)
    assert result.core_facts.issues
    assert result.inventory["device_families"].matched <= len(
        prediction.device_families
    )
    assert result.core_facts.groups["material_device_identity"].recall < 1


@pytest.mark.parametrize(
    "name",
    [
        "power conversion efficiency (PCE)",
        "open circuit voltage (Voc)",
        "fill factor (FF)",
    ],
)
def test_consistent_parenthetical_metric_aliases(name):
    assert property_name(name) == property_name(name[name.index("(") + 1 : -1])


def test_metric_qualifiers_and_missing_placeholders_are_not_values():
    assert property_name("power conversion efficiency (Voc)") != "pce"
    assert property_name("relative PCE") != "pce"
    truth = study()
    truth.performance_observations[0].metrics.append(value("not_reported", None, None))
    assert evaluate_study(truth, study()).core_facts.micro.f1 == 1
    truth.performance_observations[0].metrics[-1].raw_value = "not detected"
    assert (
        evaluate_study(truth, study()).core_facts.micro.truth
        > evaluate_study(study(), study()).core_facts.micro.truth
    )


def test_missing_treatment_input_is_unresolved_not_a_numeric_contradiction():
    from perla_extract.study_extraction.scoring_context import (
        compare_context,
        device_identity,
    )

    truth, prediction = champion()
    prediction.individual_devices[0].subject_context.conditions.pop()
    differences = compare_context(
        device_identity(truth.individual_devices[0]),
        device_identity(prediction.individual_devices[0]),
        0.01,
        0,
    )
    assert differences and {d.reason for d in differences} == {"missing_required"}
    prediction.individual_devices[0].subject_context.conditions[0].raw_value = "2 V"
    prediction.individual_devices[0].subject_context.conditions[0].value_number = 2
    differences = compare_context(
        device_identity(truth.individual_devices[0]),
        device_identity(prediction.individual_devices[0]),
        0.01,
        0,
    )
    assert any(d.reason == "conflict" for d in differences)


def test_exact_arm_labels_preserve_identity_when_stacks_alone_cannot():
    truth = study()
    control = DeviceFamily.model_validate(CASES[0]["prediction_families"][0])
    control.family_id, control.variant = "control-family", "Control"
    target = control.model_copy(deep=True)
    target.family_id, target.variant = "target-family", "Target"
    truth.device_families = [control, target]
    truth.individual_devices[0].family_id = control.family_id
    prediction = truth.model_copy(deep=True)
    prediction.device_families.reverse()
    score = evaluate_study(truth, prediction)
    pairs = [p for p in score.matches if p.kind == "device_families"]
    assert len(pairs) == 2 and all(not p.ambiguous for p in pairs)
    assert all(p.truth_id == p.predicted_id for p in pairs)
    prediction.device_families[0].variant = "unparsed arm A"
    prediction.device_families[1].variant = "unparsed arm B"
    assert any(
        p.ambiguous
        for p in evaluate_study(truth, prediction).matches
        if p.kind == "device_families"
    )


def test_a_grouped_family_is_not_expanded_to_match_three_predicted_arms():
    case = CASES[2]
    truth = StudyExtraction.model_validate_json(
        (
            ROOT
            / "data/study_extraction/ground_truth/v2/dev"
            / case["paper"]
            / "ground_truth.json"
        ).read_bytes()
    )
    prediction = truth.model_copy(deep=True)
    prediction.device_families = [
        DeviceFamily.model_validate(f) for f in case["prediction_families"]
    ]
    inventory = evaluate_study(truth, prediction).inventory["device_families"]
    assert (inventory.truth, inventory.predicted, inventory.matched) == (1, 3, 1)
