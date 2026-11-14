"""Independent identity, order and inventory controls through the public scorer."""

import itertools

import pytest

from perla_extract.study_extraction.evaluation import (
    EvaluationReport,
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.models import (
    AbsorberComponent,
    Layer,
    ProcessingStep,
)
from perla_extract.study_extraction.scoring_explanation import FactSelection
from perla_extract.study_extraction.scoring_process import ordered_alignment
from tests.study_extraction.test_evaluation import EVIDENCE, quantity, study


def recipe(operations):
    result = study()
    result.device_families[0].processing_steps = [
        ProcessingStep(
            step_id=f"s{i}",
            sequence=i,
            operation=operation,
            target_layer_ids=[],
            materials=[],
            conditions=[quantity("duration", 5 + i, "min")],
            evidence=EVIDENCE,
        )
        for i, operation in enumerate(operations, 1)
    ]
    return result


def linked_absorber():
    result = study()
    family = result.device_families[0]
    prop = quantity("bandgap", 1.6, "eV")
    family.layers = [
        Layer(
            layer_id="active",
            sequence=1,
            role="absorber",
            material="MAPbI3",
            reported_properties=[prop],
            evidence=EVIDENCE,
        )
    ]
    family.absorbers = [
        AbsorberComponent(
            absorber_id="absorber",
            layer_id="active",
            label="absorber",
            formula=None,
            properties=[prop.model_copy(deep=True)],
            constituents=[],
            evidence=EVIDENCE,
        )
    ]
    return result


def test_missing_device_properties_do_not_erase_unchanged_measurements():
    truth = study()
    truth.individual_devices[0].reported_properties = [
        quantity(f"fabrication parameter {i}", i + 1, "nm") for i in range(35)
    ]
    prediction = truth.model_copy(deep=True)
    prediction.individual_devices[0].reported_properties.clear()
    report = evaluate_study(truth, prediction)
    assert report.core_facts.groups["performance"].matched == 1
    assert report.core_facts.groups["processing"].truth == 35
    assert report.core_facts.groups["processing"].matched == 0
    assert not report.core_facts.issues


def test_outcomes_cannot_disambiguate_identical_measurement_protocols():
    truth = study()
    other = truth.performance_observations[0].model_copy(deep=True)
    other.observation_id = "repeat"
    other.metrics = [quantity("PCE", 25, "%")]
    truth.performance_observations.append(other)
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations.reverse()
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 0
    assert score.value_only.groups["performance"].matched == 2
    assert score.scoring_status == "needs_review"


def test_removed_renumbered_step_preserves_retained_facts_and_source_paths():
    truth = recipe(["cleaning", "coating", "annealing"])
    prediction = truth.model_copy(deep=True)
    steps = prediction.device_families[0].processing_steps
    steps.pop(1)
    steps[1].sequence = 2
    steps.reverse()  # Array ordering cannot override the declared process order.
    report = evaluate_study(truth, prediction)
    assert report.core_facts.groups["processing"].matched == 4
    assert report.core_facts.groups["processing"].truth == 6
    assert report.core_facts.groups["processing"].precision == 1
    assert any(
        m.truth_path.endswith("steps/2/conditions/0")
        and m.predicted_path.endswith("steps/0/conditions/0")
        for m in report.core_facts.matches
    )


def test_reversal_and_repeated_step_omissions_do_not_get_guessed_matches():
    truth = recipe(["cleaning", "annealing"])
    prediction = truth.model_copy(deep=True)
    steps = prediction.device_families[0].processing_steps
    steps[0].sequence, steps[1].sequence = 2, 1
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["processing"].matched == 0
    assert score.value_only.groups["processing"].f1 == 1
    assert score.scoring_status == "needs_review"
    truth = recipe(["annealing", "annealing"])
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps.pop(0)
    prediction.device_families[0].processing_steps[0].sequence = 1
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["processing"].matched == 0
    assert score.scoring_status == "needs_review"


def test_ordered_alignment_matches_exhaustive_independent_enumeration():
    sequences = [list(s) for n in range(5) for s in itertools.product("AB", repeat=n)]
    for left in sequences:
        for right in sequences:
            alignments = []
            for size in range(min(len(left), len(right)) + 1):
                for a in itertools.combinations(range(len(left)), size):
                    for b in itertools.combinations(range(len(right)), size):
                        edges = set(zip(a, b))
                        if all(left[i] == right[j] for i, j in edges):
                            alignments.append(edges)
            best = max(map(len, alignments))
            optimal = [edges for edges in alignments if len(edges) == best]
            mandatory = set.intersection(*optimal)
            possible = set.union(*optimal)
            assert ordered_alignment(left, right) == (mandatory, possible - mandatory)


def test_sonication_method_can_move_between_action_and_condition():
    truth = recipe(["sonication cleaning of ITO substrates"])
    family = truth.device_families[0]
    family.layers = [
        Layer(
            layer_id="ito",
            sequence=1,
            role="substrate",
            material="ITO",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    ]
    step = family.processing_steps[0]
    step.target_layer_ids = ["ito"]
    step.materials = ["water", "acetone"]
    prediction = truth.model_copy(deep=True)
    actual = prediction.device_families[0].processing_steps[0]
    actual.operation = "cleaning"
    method = quantity("Method", None, None)
    method.raw_value = "sonication"
    actual.conditions.append(method)
    path = "/device_families/0/processing_steps/0/conditions/1"
    report, explanation = evaluate_study_with_explanations(
        truth, prediction, facts=[FactSelection(side="prediction", path=path)]
    )
    assert report.core_facts.groups["processing"].f1 == 1
    assert explanation.facts[0].selection.path == path
    assert explanation.facts[0].reason == "matched"
    assert explanation.facts[0].canonical_path.endswith("/operation")
    assert len(explanation.facts[0].source_paths) == 2
    actual.conditions.pop()
    assert (
        evaluate_study(truth, prediction).core_facts.groups["processing"].matched == 0
    )


@pytest.mark.parametrize("wrong_target", ["FTO", "ITO and FTO"])
def test_operation_target_qualifier_cannot_be_discarded(wrong_target):
    truth = recipe([f"sonication cleaning of {wrong_target} substrates"])
    truth.device_families[0].layers = [
        Layer(
            layer_id="ito",
            sequence=1,
            role="substrate",
            material="ITO",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    ]
    truth.device_families[0].processing_steps[0].target_layer_ids = ["ito"]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps[0].operation = "sonication cleaning"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["processing"].matched == 0
    )


@pytest.mark.parametrize("location", ["layer", "absorber"])
def test_redundant_linked_property_counts_once_and_can_be_explained(location):
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    family = prediction.device_families[0]
    if location == "layer":
        family.absorbers[0].properties.clear()
    else:
        family.layers[0].reported_properties.clear()
    before = truth.model_dump_json(), prediction.model_dump_json()
    alias = "/device_families/0/absorbers/0/properties/0"
    report, explanation = evaluate_study_with_explanations(
        truth, prediction, facts=[FactSelection(side="truth", path=alias)]
    )
    assert report.core_facts.groups["composition"].truth == 1
    assert report.core_facts.micro.f1 == 1
    assert explanation.facts[0].selection.path == alias
    assert explanation.facts[0].reason == "matched"
    assert len(explanation.facts[0].source_paths) == 2
    assert (
        report.core_facts.fact_sources["truth"][explanation.facts[0].canonical_path]
        == explanation.facts[0].source_paths
    )
    assert EvaluationReport.model_validate_json(report.model_dump_json()) == report
    assert (truth.model_dump_json(), prediction.model_dump_json()) == before


def test_duplicates_within_one_representation_still_cost_precision():
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    props = prediction.device_families[0].layers[0].reported_properties
    props.append(props[0].model_copy(deep=True))
    score = evaluate_study(truth, prediction).core_facts.groups["composition"]
    assert (score.matched, score.predicted, score.truth) == (1, 2, 1)


def test_conflicting_linked_properties_are_flagged_and_not_cherry_picked():
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].properties[0] = quantity(
        "bandgap", 1.8, "eV"
    )
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["composition"].matched == 0
    assert score.groups["composition"].predicted == 2
    assert score.value_only.groups["composition"].matched == 1
    assert any(i.kind == "conflicting_representations" for i in score.issues)


def test_metric_names_and_omissions_cannot_resolve_repeated_protocols():
    truth = study()
    first = truth.performance_observations[0]
    first.metrics.append(quantity("FF", 80, "%"))
    second = first.model_copy(deep=True)
    second.observation_id = "repeat"
    second.metrics = [quantity("Jsc", 25, "mA/cm2")]
    truth.performance_observations.append(second)
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics.pop()
    prediction.performance_observations.reverse()
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 0
    assert score.value_only.groups["performance"].matched == 2
    assert score.scoring_status == "needs_review"


def test_omitting_redundant_absorber_object_preserves_layer_properties():
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers.clear()
    report = evaluate_study(truth, prediction)
    assert report.core_facts.micro.f1 == 1
    assert report.core_facts.groups["composition"].truth == 1


def test_linked_constituents_merge_but_distinct_layers_stay_separate():
    from perla_extract.study_extraction.models import MaterialConstituent

    truth = linked_absorber()
    family = truth.device_families[0]
    constituent = MaterialConstituent(
        name="PbI2",
        role="precursor",
        amount=quantity("concentration", 1, "mol/L"),
        evidence=EVIDENCE,
    )
    family.layers[0].constituents = [constituent]
    family.absorbers[0].constituents = [constituent.model_copy(deep=True)]
    second = family.layers[0].model_copy(deep=True)
    second.layer_id, second.sequence = "other", 2
    family.layers.append(second)
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].constituents.clear()
    score = evaluate_study(truth, prediction).core_facts
    assert score.micro.f1 == 1
    assert score.groups["composition"].truth == 8  # Two layers, four facts each.
    assert not score.issues


@pytest.mark.parametrize(
    "damage", ["missing_fact", "missing_canonical", "reused_source"]
)
def test_fact_source_ledger_rejects_missing_or_reused_fields(damage):
    from pydantic import ValidationError

    payload = evaluate_study(linked_absorber(), linked_absorber()).model_dump()
    sources = payload["core_facts"]["fact_sources"]["truth"]
    path = "/device_families/0/layers/0/reported_properties/0"
    if damage == "missing_fact":
        sources.pop(path)
    elif damage == "missing_canonical":
        sources[path].remove(path)
    else:
        sources[path].append("/device_families/0/layers/0/material")
    with pytest.raises(ValidationError, match="source"):
        EvaluationReport.model_validate(payload)


@pytest.mark.parametrize(
    "left,right",
    [
        (quantity("thickness", 600, "nm"), quantity("thickness", 6e-7, "m")),
        (quantity("thickness", 0.1, "nm"), quantity("thickness", 1, "angstrom")),
        (
            quantity("concentration", 1, "mol/L"),
            quantity("concentration", 1000, "mol/m3"),
        ),
    ],
)
def test_linked_equivalent_units_merge_despite_conversion_roundoff(left, right):
    truth = linked_absorber()
    truth.device_families[0].layers[0].reported_properties = [left]
    truth.device_families[0].absorbers[0].properties = [right]
    score = evaluate_study(truth, truth).core_facts
    assert score.groups["composition"].truth == 1
    assert score.micro.f1 == 1
    assert not score.issues


def test_scientific_tolerance_cannot_merge_distinct_linked_claims():
    from perla_extract.study_extraction.evaluation import EvaluationConfig

    truth = linked_absorber()
    truth.device_families[0].absorbers[0].properties[0] = quantity(
        "bandgap", 1.6000001, "eV"
    )
    for tolerance in (0, 1e-6, 0.1):
        score = evaluate_study(
            truth, truth, config=EvaluationConfig(numeric_relative_tolerance=tolerance)
        ).core_facts
        assert score.groups["composition"].truth == 2
        assert score.groups["composition"].matched == 0
        assert all(
            issue.kind == "conflicting_representations" for issue in score.issues
        )


@pytest.mark.parametrize("view", ["value_only", "attribution"])
def test_diagnostic_views_cannot_substitute_a_different_fact_ledger(view):
    from pydantic import ValidationError

    payload = evaluate_study(linked_absorber(), linked_absorber()).model_dump()
    payload["core_facts"][view]["matches"][0]["predicted_path"] = "/invented/path"
    with pytest.raises(ValidationError, match="same canonical facts"):
        EvaluationReport.model_validate(payload)


@pytest.mark.parametrize("operation", ["annealed", "annealing"])
def test_annealing_time_alias_is_local_to_the_identified_operation(operation):
    truth = recipe(["annealing"])
    truth.device_families[0].processing_steps[0].conditions[0].name = "annealing time"
    prediction = truth.model_copy(deep=True)
    step = prediction.device_families[0].processing_steps[0]
    step.operation = operation
    step.conditions[0].name = "annealing duration"
    assert evaluate_study(truth, prediction).core_facts.groups["processing"].f1 == 1
    step.operation = "annealing ramp"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["processing"].matched == 0
    )


def test_missing_ingredients_do_not_erase_identified_annealing_time_but_do_block_ratio():
    truth = recipe(["annealing"])
    step = truth.device_families[0].processing_steps[0]
    step.materials = ["PbI2", "MAI"]
    step.conditions.append(quantity("precursor ratio", 1, "dimensionless"))
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps[0].materials.clear()
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["processing_operations"].matched == 1
    assert score.subfields["processing_conditions"].matched == 1  # Duration only.
    assert score.value_only.subfields["processing_conditions"].matched == 2
