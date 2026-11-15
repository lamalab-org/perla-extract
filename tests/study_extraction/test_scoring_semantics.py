"""Scientific counterexamples for recovery, attribution, coverage and reporting."""

from unittest.mock import patch

import pytest

from examples.scoring.run import EVIDENCE, quantity, seed_study, synthetic_coverage
from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    EvaluationReport,
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.models import (
    PopulationStatistic,
    ReportedValue,
    SubjectContext,
)
from perla_extract.study_extraction.scoring_context import (
    FactContext,
    compare_context,
    equal_value,
)
from perla_extract.study_extraction.scoring_coverage import (
    DomainCoverage,
    ReferenceCoverage,
)
from perla_extract.study_extraction.scoring_explanation import FactSelection


def population_study():
    result = seed_study()
    result.performance_observations.clear()
    result.population_statistics = [
        PopulationStatistic(
            population_id="batch",
            family_id="family",
            label="Nitrogen-treated films",
            statistic_type="mean",
            sample_size=4,
            metrics=[quantity("efficiency retention", 93)],
            evidence=EVIDENCE,
        )
    ]
    return result


def subject(atmosphere="N2", stage="before_device_completion"):
    return SubjectContext(
        treatment_stage=stage,
        treated_material="MAPbI3",
        conditions=[
            ReportedValue(
                name="atmosphere",
                raw_value=atmosphere,
                value_number=None,
                unit=None,
                evidence=EVIDENCE,
            )
        ],
    )


def test_cross_container_recovery_does_not_credit_wrong_scientific_attribution():
    truth, prediction = population_study(), seed_study()
    observation = prediction.performance_observations[0]
    observation.metrics = [quantity("reference efficiency retained", 93)]
    observation.measurement_type = "stabilized_power_output"
    path = "/population_statistics/0/metrics/0"
    score, why = evaluate_study_with_explanations(
        truth,
        prediction,
        facts=[
            FactSelection(side="truth", path=path, view=view)
            for view in ("attributed", "recovery")
        ],
    )
    assert score.core_facts.recovery.subfields["relative_performance"].matched == 1
    assert score.core_facts.subfields["relative_performance"].matched == 0
    assert why.facts[1].reason == "matched"
    differences = why.facts[0].candidates[0].context_mismatches
    assert any(
        d.component == "statistical_level" and d.reason == "conflict"
        for d in differences
    )
    match = score.core_facts.recovery.matches[-1]
    assert match.truth_container == "population_statistics"
    assert match.predicted_container == "performance_observations"
    assert EvaluationReport.model_validate_json(score.model_dump_json()) == score


def test_duplicate_recovery_cannot_increase_recall():
    truth, prediction = population_study(), seed_study()
    prediction.performance_observations[0].metrics = [quantity("PCE retention", 93)] * 2
    scores = evaluate_study(truth, prediction).core_facts.recovery.subfields[
        "relative_performance"
    ]
    assert (scores.matched, scores.predicted, scores.truth) == (1, 2, 1)
    assert scores.precision == 0.5


def test_absolute_efficiency_cannot_recover_relative_retention():
    prediction = seed_study()
    prediction.performance_observations[0].metrics = [quantity("PCE", 93)]
    assert (
        evaluate_study(population_study(), prediction)
        .core_facts.recovery.groups["performance"]
        .matched
        == 0
    )


def test_reporting_category_does_not_select_candidates():
    truth = population_study()
    original = evaluate_study(truth, truth).core_facts
    # Changing a display bin for all facts cannot change which paths earn credit.
    with patch(
        "perla_extract.study_extraction.scoring_facts.fact_subfield",
        return_value="other_measurement_fields",
    ):
        changed = evaluate_study(truth, truth).core_facts
    assert changed.micro == original.micro
    assert {(m.truth_path, m.predicted_path) for m in changed.matches} == {
        (m.truth_path, m.predicted_path) for m in original.matches
    }


def test_named_context_is_order_independent_but_not_name_independent():
    a = FactContext.of(atmosphere="N2", specimen="film")
    b = FactContext.of(specimen="film", atmosphere="N2")
    assert equal_value(a, b, 0.01, 0)
    changed = FactContext.of(unrelated="N2", specimen="film")
    differences = compare_context(a, changed, 0.01, 0)
    assert {d.name for d in differences} == {"atmosphere", "unrelated"}
    assert all(d.reason == "missing_required" for d in differences)
    with pytest.raises(ValueError, match="unique"):
        a.extend(FactContext.of(specimen="device"))


def test_structured_identity_allows_label_paraphrase_without_using_outcomes():
    truth = population_study()
    truth.population_statistics[0].subjects = [subject()]
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].label = "Cohort A"
    assert evaluate_study(truth, prediction).core_facts.micro.f1 == 1
    prediction.population_statistics[0].metrics[0] = quantity(
        "efficiency retention", 70
    )
    score = evaluate_study(truth, prediction)
    assert score.inventory["population_statistics"].matched == 1
    assert score.core_facts.subfields["relative_performance"].matched == 0


@pytest.mark.parametrize(
    "different", [subject("O2"), subject(stage="completed_device")]
)
def test_treatment_or_material_stage_conflict_blocks_attribution(different):
    truth = population_study()
    truth.population_statistics[0].subjects = [subject()]
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].subjects = [different]
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["relative_performance"].matched == 0
    assert score.recovery.subfields["relative_performance"].matched == 1


def test_shared_assertion_cannot_expand_into_multiple_cohort_matches():
    truth = population_study()
    first = truth.population_statistics[0]
    first.subjects = [subject()]
    second = first.model_copy(deep=True)
    second.population_id, second.label, second.subjects = (
        "oxygen",
        "Oxygen",
        [subject("O2")],
    )
    truth.population_statistics.append(second)
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics = [first.model_copy(deep=True)]
    prediction.population_statistics[0].subjects = [subject(), subject("O2")]
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["relative_performance"].matched == 0
    assert score.recovery.subfields["relative_performance"].matched == 1


def test_equal_outcomes_do_not_resolve_ambiguous_structured_cohorts():
    truth = population_study()
    truth.population_statistics[0].subjects = [subject()]
    other = truth.population_statistics[0].model_copy(deep=True)
    other.population_id = "repeat"
    other.metrics = [quantity("efficiency retention", 78)]
    truth.population_statistics.append(other)
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics.reverse()
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["relative_performance"].matched == 0
    assert score.recovery.subfields["relative_performance"].matched == 2
    assert score.scoring_status == "needs_review"


def test_misplaced_outcomes_in_subject_conditions_cannot_resolve_identity():
    truth = population_study()
    first = truth.population_statistics[0]
    first.subjects = [subject()]
    second = first.model_copy(deep=True)
    second.population_id = "repeat"
    first.subjects[0].conditions.append(quantity("PCE", 20))
    second.subjects[0].conditions.append(quantity("PCE", 25))
    truth.population_statistics.append(second)
    score = evaluate_study(truth, truth).core_facts
    assert score.scoring_status == "needs_review"
    assert score.subfields["relative_performance"].matched == 0
    assert score.recovery.subfields["relative_performance"].matched == 2


def test_processing_recovery_uses_one_bin_across_recipe_and_specimen_fields():
    from perla_extract.study_extraction.models import ProcessingStep

    truth = seed_study()
    value = quantity("temperature", 85).model_copy(
        update={"raw_value": "85 C", "unit": "C"}
    )
    truth.device_families[0].processing_steps = [
        ProcessingStep(
            step_id="heat",
            sequence=1,
            operation="annealing",
            target_layer_ids=[],
            materials=[],
            conditions=[value],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps.clear()
    prediction.individual_devices[0].reported_properties = [value]
    score = evaluate_study(truth, prediction).core_facts
    assert score.recovery.subfields["processing_conditions"].matched == 1
    assert score.subfields["processing_conditions"].matched == 0
    assert score.recovery.subfields["processing_conditions"].predicted == 1


def test_literal_equivalence_cannot_produce_different_reporting_denominators():
    truth = seed_study()
    truth.performance_observations[0].metrics = [
        ReportedValue(
            name="unknown material observation",
            raw_value="Fe₃",
            unit=None,
            value_number=None,
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics[0].raw_value = "Fe3"
    assert evaluate_study(truth, prediction).core_facts.recovery.micro.f1 == 1


def test_metadata_and_qualitative_trends_are_not_numerical_outcomes():
    truth = population_study()
    population = truth.population_statistics[0]
    population.error_bar_type = "standard_deviation"
    population.metrics.append(
        ReportedValue(
            name="Voc",
            raw_value="little affected",
            value_number=None,
            unit=None,
            evidence=EVIDENCE,
        )
    )
    score = evaluate_study(truth, truth).core_facts
    assert score.subfields["relative_performance"].truth == 1
    assert score.subfields["statistical_metadata"].truth == 2
    assert score.subfields["qualitative_observations"].truth == 1
    assert score.subfields["photovoltaic_metrics"].truth == 0
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].error_bar_type = "standard_error"
    assert (
        evaluate_study(truth, prediction)
        .core_facts.subfields["statistical_metadata"]
        .matched
        == 1
    )


def test_missing_optional_metadata_loses_its_own_point_only():
    truth = population_study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].sample_size = None
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["relative_performance"].matched == 1
    assert score.subfields["statistical_metadata"].matched == 0


def test_error_bar_type_can_move_from_a_metric_into_its_structured_field():
    truth = population_study()
    truth.population_statistics[0].error_bar_type = "standard_deviation"
    prediction = truth.model_copy(deep=True)
    population = prediction.population_statistics[0]
    population.error_bar_type = "not_reported"
    population.metrics.append(
        ReportedValue(
            name="error-bar statistic for photovoltaic parameters",
            raw_value="standard deviation",
            value_number=None,
            unit=None,
            evidence=EVIDENCE,
        )
    )
    assert evaluate_study(truth, prediction).core_facts.micro.f1 == 1
    # Parsed fields that contradict the categorical statement cannot be erased.
    population.metrics[-1].value_number = 10.0
    assert (
        evaluate_study(truth, prediction)
        .core_facts.recovery.subfields["statistical_metadata"]
        .matched
        == 1
    )


def test_unknown_reference_coverage_is_not_verified_by_perfect_self_agreement():
    truth = population_study()
    provenance = BenchmarkProvenance(
        paper_id="synthetic",
        split="dev",
        ground_truth_sha256="0" * 64,
        source_manifest_sha256="1" * 64,
    )
    score = evaluate_study(truth, truth, benchmark=provenance)
    assert score.core_facts.micro.f1 == 1
    assert score.interpretation == "reference_agreement"
    assert any("coverage" in warning for warning in score.input_warnings)
    provenance.coverage = synthetic_coverage()
    assert (
        evaluate_study(truth, truth, benchmark=provenance).interpretation
        == "reviewed_scope"
    )
    provenance.coverage.domains["performance"].figures = "partial"
    assert (
        evaluate_study(truth, truth, benchmark=provenance).interpretation
        == "reference_agreement"
    )
    with pytest.raises(ValueError, match="rationale"):
        DomainCoverage(text_and_tables="complete")
    assert not ReferenceCoverage().complete()
