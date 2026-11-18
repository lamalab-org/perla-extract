"""Scientific subjects survive changes to the containers used to serialize facts."""

import itertools

import pytest

from perla_extract.study_extraction.evaluation import (
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.scoring_explanation import FactSelection
from tests.study_extraction.test_evaluation import quantity, study


def observations():
    truth = study()
    first = truth.performance_observations[0]
    first.metrics.append(quantity("FF", 80, "%"))
    second = first.model_copy(deep=True)
    second.observation_id = "repeat"
    second.metrics = [quantity("PCE", 25, "%"), quantity("FF", 70, "%")]
    truth.performance_observations.append(second)
    return truth


def test_split_and_merged_observations_have_the_same_fact_inventory_and_credit():
    truth = observations()
    for order in itertools.permutations(range(4)):
        prediction = truth.model_copy(deep=True)
        metrics = [v for o in prediction.performance_observations for v in o.metrics]
        prediction.performance_observations = [prediction.performance_observations[0]]
        prediction.performance_observations[0].metrics = [metrics[i] for i in order]
        score = evaluate_study(truth, prediction).core_facts
        assert score.micro.f1 == 1
        assert score.groups["performance"].matched == 4
        assert score.groups == score.recovery.groups
        assert evaluate_study(prediction, truth).core_facts.micro == score.micro


def test_duplicate_containers_preserve_precision_penalty():
    truth = study()
    prediction = truth.model_copy(deep=True)
    duplicate = prediction.performance_observations[0].model_copy(deep=True)
    duplicate.observation_id = "extra"
    prediction.performance_observations.append(duplicate)
    score = evaluate_study(truth, prediction).core_facts.groups["performance"]
    assert (score.truth, score.predicted, score.matched) == (1, 2, 1)
    assert score.precision == 0.5
    assert score.recall == 1


@pytest.mark.parametrize("link", [None, "missing-device"])
def test_missing_or_broken_specimen_link_cannot_receive_attributed_credit(link):
    truth = observations()
    prediction = truth.model_copy(deep=True)
    for observation in prediction.performance_observations:
        observation.device_id = link
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 0
    assert score.recovery.groups["performance"].matched == 4


def test_ambiguous_devices_remain_ambiguous_after_container_pairing_is_removed():
    truth = observations()
    device = truth.individual_devices[0].model_copy(deep=True)
    device.device_id = "other-specimen"
    truth.individual_devices.append(device)
    truth.performance_observations[1].device_id = device.device_id
    score = evaluate_study(truth, truth).core_facts
    assert score.groups["performance"].matched == 0
    assert score.scoring_status == "needs_review"


def test_values_cannot_move_between_distinct_specimens():
    truth = observations()
    device = truth.individual_devices[0].model_copy(deep=True)
    device.device_id, device.variant, device.label = "treated", "treated", "treated"
    truth.individual_devices.append(device)
    truth.performance_observations[1].device_id = device.device_id
    prediction = truth.model_copy(deep=True)
    a, b = prediction.performance_observations
    a.metrics, b.metrics = b.metrics, a.metrics
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 0
    assert score.recovery.groups["performance"].matched == 4


def test_explanations_use_the_actual_multiset_assignment_and_bound_only_display():
    truth = observations()
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations.reverse()
    expected = evaluate_study(truth, prediction)
    selection = FactSelection(
        side="truth", path="/performance_observations/0/metrics/0"
    )
    for limit in (1, 20):
        actual, why = evaluate_study_with_explanations(
            truth, prediction, facts=[selection], max_candidates=limit
        )
        assert actual == expected
        assert why.facts[0].reason == "matched"
        assert why.facts[0].owner.startswith("individual_devices:")
        assert why.facts[0].owner_matched
