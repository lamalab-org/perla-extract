"""Whole-material aliases must not manufacture composition or hide contradictions."""

import pytest
from test_scoring_structure import linked_absorber

from perla_extract.study_extraction.evaluation import (
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.models import MaterialConstituent
from perla_extract.study_extraction.scoring_explanation import FactSelection
from tests.study_extraction.test_evaluation import EVIDENCE, quantity


def constituent(name="MAPbI3", role=None):
    return MaterialConstituent(name=name, role=role, amount=None, evidence=EVIDENCE)


def text_claim(name, text):
    value = quantity(name, None, None)
    value.raw_value = text
    return value


def test_one_material_has_one_identity_point_with_every_source_path_retained():
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    family = prediction.device_families[0]
    family.layers[0].constituents = [constituent()]
    family.absorbers[0].constituents = [constituent()]
    family.absorbers[0].formula = text_claim("formula", "methylammonium lead triiodide")
    path = "/device_families/0/absorbers/0/formula"
    report, explanation = evaluate_study_with_explanations(
        truth, prediction, facts=[FactSelection(side="prediction", path=path)]
    )
    identity = report.core_facts.subfields["material_identity"]
    assert (identity.truth, identity.predicted, identity.matched) == (1, 1, 1)
    assert report.core_facts.micro.f1 == 1
    assert report.matches[0].similarity == 1
    assert len(explanation.facts[0].source_paths) == 4
    assert explanation.facts[0].reason == "matched"


def test_chlorine_film_property_is_one_claim_across_linked_containers():
    truth = linked_absorber()
    family = truth.device_families[0]
    family.layers[0].reported_properties = [
        text_claim("chlorine in resulting perovskite film", "absent")
    ]
    family.absorbers[0].properties = [
        text_claim("chlorine in resulting perovskite films", "absent")
    ]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].properties.clear()
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["composition_properties"].truth == 1
    assert score.micro.f1 == 1
    prediction.device_families[0].layers[0].reported_properties[0].raw_value = "present"
    assert (
        evaluate_study(truth, prediction)
        .core_facts.subfields["composition_properties"]
        .matched
        == 0
    )


@pytest.mark.parametrize("role", ["precursor", "A-site cation", "additive"])
def test_nonwhole_material_roles_are_not_merged_into_layer_identity(role):
    truth = linked_absorber()
    truth.device_families[0].layers[0].constituents = [constituent(role=role)]
    score = evaluate_study(truth, truth).core_facts
    assert len(score.fact_sources["truth"]["/device_families/0/layers/0/material"]) == 1


def test_precursor_amount_does_not_become_finished_film_composition():
    truth = linked_absorber()
    member = constituent("PbI2", "precursor")
    member.amount = quantity("mole fraction", 0.5, "dimensionless")
    truth.device_families[0].absorbers[0].constituents = [member]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].constituents[0].role = "absorber"
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["recipe_composition"].matched == 0
    assert score.value_only.subfields["recipe_composition"].matched == 0
    assert score.subfields["composition_amounts"].matched == 0


def test_incompatible_formula_and_layer_cannot_cherry_pick_identity_credit():
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].formula = text_claim("formula", "TiO2")
    score = evaluate_study(truth, prediction).core_facts
    assert any(i.kind == "conflicting_representations" for i in score.issues)
    assert score.subfields["material_identity"].matched == 0


@pytest.mark.parametrize("role, matched", [("perovskite absorber", 1), ("contact", 0)])
def test_role_equivalence_preserves_distinct_functions(role, matched):
    truth = linked_absorber()
    truth.device_families[0].layers[0].constituents = [constituent(role="absorber")]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].layers[0].constituents[0].role = role
    assert (
        evaluate_study(truth, prediction).core_facts.subfields["material_roles"].matched
        == matched
    )


def test_conflicting_whole_constituent_does_not_leave_a_correct_layer_answer_to_pick():
    truth = linked_absorber()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].layers[0].constituents = [
        constituent("TiO2", "absorber")
    ]
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["material_identity"].matched == 0
    assert score.scoring_status == "needs_review"
