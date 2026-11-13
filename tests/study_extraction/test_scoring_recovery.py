"""Hand-checkable representation changes and scientific counterexamples.

These expectations are defined on synthetic records, independently of any live
model output. The reviewed paper files are not modified to obtain passing scores.
"""

import pytest

from perla_extract.study_extraction.evaluation import (
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.models import Layer, ReportedValue
from perla_extract.study_extraction.scoring_explanation import FactSelection
from perla_extract.study_extraction.scoring_facts import equal_value
from perla_extract.study_extraction.scoring_vocabulary import (
    material_name,
    scoped_property_name,
)
from tests.study_extraction.test_evaluation import (
    EVIDENCE,
    quantity,
    scientific_study,
    study,
)
from tests.study_extraction.test_scoring_contracts import study as cohort_study


def raw_quantity(raw, number=None, unit=None):
    return ReportedValue(
        name="thickness",
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


@pytest.mark.parametrize(
    "raw,number,unit,expected",
    [
        ("125 nm", None, None, True),
        ("125 nm", 125, None, True),
        ("125 nm", None, "nanometer", True),
        ("125", None, "nm", True),
        ("0.125 µm", None, None, True),
        ("125", None, None, False),
        ("125 nm", 126, None, False),
        ("125 nm", None, "m", False),
        ("125 ± 5 nm", None, None, False),
        (">125 nm", None, None, False),
        ("about 125 nm", None, None, False),
        ("125 nm SD", None, None, False),
        ("125-130 nm", None, None, False),
    ],
)
def test_raw_quantity_preserves_explicit_information_without_inventing_it(
    raw, number, unit, expected
):
    parsed = raw_quantity("125 nm", 125, "nm")
    literal = raw_quantity(raw, number, unit)
    original = literal.model_dump()
    assert equal_value(parsed, literal, 1e-6, 0) is expected
    assert equal_value(literal, parsed, 1e-6, 0) is expected
    assert literal.model_dump() == original


def test_unitless_raw_scalar_remains_unitless_after_parsing():
    literal = raw_quantity("0.2")
    assert equal_value(literal, raw_quantity("0.2", 0.2), 1e-6, 0)
    assert not equal_value(literal, raw_quantity("20%", 20, "%"), 1e-6, 0)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Silver (Ag)", "silver"),
        ("silver electrode", "Ag"),
        ("Tin dioxide", "SnO₂"),
        ("ITO substrate", "indium tin oxide"),
        ("poly‑3‑hexylthiophene", "P3HT"),
        ("TiO 2", "TiO2"),
    ],
)
def test_checked_material_names_and_notation_agree(a, b):
    assert material_name(a) == material_name(b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Gold (Ag)", "gold"),
        ("CoO", "COO"),
        ("Fe³⁺", "Fe₃⁺"),
        ("doped SnO2", "SnO2"),
        ("TiO2 nanoparticles", "TiO2"),
        ("silver/gold", "Ag"),
        ("ITO/Ag", "Ag/ITO"),
    ],
)
def test_material_equivalence_does_not_erase_scientific_distinctions(a, b):
    assert material_name(a) != material_name(b)


def test_property_qualifier_must_name_the_actual_layer_material():
    assert scoped_property_name("silver contact thickness", "Ag") == "thickness"
    assert scoped_property_name("gold contact thickness", "Ag") != "thickness"
    assert scoped_property_name("ITO sheet resistance", "ITO") == "sheetresistance"
    assert scoped_property_name("ITO sheet resistance", "SnO2") != "sheetresistance"
    assert scoped_property_name("TIO2 layer thickness", "TiO2") != "thickness"
    assert scoped_property_name("TiO2 layer thickness", "TiO2") == "thickness"


def test_equivalent_layer_property_gets_strict_credit_and_correct_explanation():
    truth = study()
    family = truth.device_families[0]
    family.layers = [
        Layer(
            layer_id="metal",
            sequence=1,
            role="back_electrode",
            material="silver",
            reported_properties=[raw_quantity("125 nm", 125, "nm")],
            evidence=EVIDENCE,
        )
    ]
    family.layers[0].reported_properties[0].name = "silver contact thickness"
    prediction = truth.model_copy(deep=True)
    layer = prediction.device_families[0].layers[0]
    layer.material = "Silver (Ag)"
    layer.reported_properties[0] = raw_quantity("0.125 µm")
    original = truth.model_dump_json(), prediction.model_dump_json()
    path = "/device_families/0/layers/0/reported_properties/0"
    report, explanation = evaluate_study_with_explanations(
        truth, prediction, facts=[FactSelection(side="truth", path=path)]
    )
    assert report.core_facts.micro.f1 == 1
    assert explanation.facts[0].reason == "matched"
    assert (
        explanation.facts[0].candidates[0].predicted_claim["literal"]["number"] is None
    )
    assert (
        explanation.facts[0].candidates[0].predicted_claim["literal"]["source_unit"]
        is None
    )
    assert explanation.facts[0].candidates[0].predicted_claim["unit"] == "µm"
    assert (truth.model_dump_json(), prediction.model_dump_json()) == original
    layer.material = "gold"
    # A correct number on the wrong material cannot receive strict property credit.
    result = evaluate_study(truth, prediction)
    assert path in result.core_facts.unmatched_truth_paths


def test_missing_family_properties_do_not_erase_unchanged_device_results():
    truth = scientific_study()
    truth.device_families[0].layers[0].reported_properties = [
        quantity(f"independent property {i}", i + 1, "nm") for i in range(35)
    ]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].layers[0].reported_properties.clear()
    prediction.device_families[0].label = "A shorter display heading"
    result = evaluate_study(truth, prediction)
    assert result.core_facts.groups["performance"].f1 == 1
    assert (
        result.core_facts.groups["stack"].truth
        - result.core_facts.groups["stack"].matched
        == 35
    )


def test_family_identity_preserves_chemical_case_and_record_order_invariance():
    truth = study()
    family = truth.device_families[0]
    family.layers = [
        Layer(
            layer_id="oxide",
            sequence=1,
            role="electron_transport_layer",
            material="CoO",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    ]
    other = family.model_copy(deep=True)
    other.family_id = "other-family"
    other.layers[0].material = "COO"
    truth.device_families.append(other)
    prediction = truth.model_copy(deep=True)
    prediction.device_families.reverse()
    score = evaluate_study(truth, prediction)
    assert score.core_facts.micro.f1 == 1
    assert not score.core_facts.issues


def test_value_recovery_is_independent_of_unmatched_cohort_labels():
    truth = cohort_study()
    prediction = truth.model_copy(deep=True)
    for cohort in prediction.population_statistics:
        cohort.label = "An unresolved label for " + cohort.label
    path = "/population_statistics/0/metrics/0"
    report, explanation = evaluate_study_with_explanations(
        truth,
        prediction,
        facts=[
            FactSelection(side="truth", path=path, view=view)
            for view in ("strict", "value")
        ],
    )
    assert report.core_facts.groups["population"].matched == 0
    assert report.core_facts.value_only.groups["population"].f1 == 1
    assert [f.reason for f in explanation.facts] == ["owner_unmatched", "matched"]
    assert not explanation.facts[1].owner_matched
    assert report.core_facts.scoring_status == "needs_review"
    assert any(i.kind == "unmatched_records" for i in report.core_facts.issues)


def test_recovery_cannot_credit_one_value_twice_or_replace_attribution():
    truth = cohort_study()
    prediction = truth.model_copy(deep=True)
    left, right = prediction.population_statistics
    left.metrics, right.metrics = right.metrics, left.metrics
    score = evaluate_study(truth, prediction).core_facts
    # Two sample sizes remain correct; all eight values have the wrong treatment.
    assert score.groups["population"].matched == 2
    assert score.value_only.groups["population"].matched == 10
    right.metrics.append(right.metrics[0].model_copy(deep=True))
    score = evaluate_study(truth, prediction).core_facts
    assert score.value_only.groups["population"].matched == 10
    assert score.value_only.groups["population"].predicted == 11


def test_relabeling_an_individual_measurement_as_population_is_not_correct():
    from perla_extract.study_extraction.models import PopulationStatistic

    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics = [
        PopulationStatistic(
            population_id="invented-population",
            family_id=prediction.device_families[0].family_id,
            label="invented population",
            statistic_type="mean",
            sample_size=4,
            metrics=prediction.performance_observations[0].metrics,
            evidence=EVIDENCE,
        )
    ]
    prediction.performance_observations.clear()
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["performance"].matched == 0
    assert score.groups["population"].matched == 0
