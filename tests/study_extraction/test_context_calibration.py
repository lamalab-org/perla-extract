"""The context contract must survive changes that should not change the science."""

import pytest

from examples.scoring.calibration import calibration_cases, check_case
from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.scoring_protocol import canonical_conditions
from tests.study_extraction.test_evaluation import quantity, study


@pytest.mark.parametrize("case", list(calibration_cases(study())), ids=lambda c: c.name)
def test_generated_context_contract(case):
    result = check_case(case, evaluate_study)
    assert result["passed"], result["failures"]


@pytest.mark.parametrize("extra", [100, 100.5, 50])
def test_only_roundoff_equal_copies_across_containers_are_redundant(extra):
    truth = study()
    record = truth.performance_observations[0]
    record.metrics.append(quantity("irradiance", 100, "mW/cm2"))
    record.measurement_conditions.append(
        quantity("illumination intensity", extra, "mW/cm2")
    )
    entries = canonical_conditions(record, "/performance_observations/0")
    assert len(entries) == (1 if extra == 100 else 2)
    report = evaluate_study(truth, truth).core_facts
    assert bool(report.issues) == (extra != 100)
    assert report.subfields["measurement_protocol"].truth == len(entries)
    if extra == 100:
        assert len(entries[0].source_paths) == 2


def test_duplicate_conditions_within_one_container_keep_multiplicity():
    truth = study()
    condition = quantity("scan rate", 100, "mV/s")
    truth.performance_observations[0].measurement_conditions = [condition, condition]
    assert len(canonical_conditions(truth.performance_observations[0])) == 2


def test_unfamiliar_condition_stays_literal_and_cannot_become_an_outcome():
    truth = study()
    record = truth.performance_observations[0]
    record.measurement_conditions = [quantity("unfamiliar protocol", 2, "V")]
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics.append(
        prediction.performance_observations[0].measurement_conditions.pop()
    )
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["measurement_protocol"].matched == 0
    assert score.recovery.subfields["measurement_protocol"].matched == 0
    assert score.diagnostics.coverage["truth"].unsupported_condition_names == 1


def test_outcomes_misplaced_in_conditions_cannot_disambiguate_identity():
    from perla_extract.study_extraction.scoring_identity import record_similarity

    left = study().performance_observations[0]
    right = left.model_copy(deep=True)
    left.measurement_conditions = [quantity("PCE", 20, "%")]
    right.measurement_conditions = [quantity("PCE", 10, "%")]
    assert record_similarity(left, right, 0.01, 0) == 1
    left.measurement_conditions = [quantity("irradiance", 100, "mW/cm2")]
    right.measurement_conditions = [quantity("irradiance", 50, "mW/cm2")]
    assert record_similarity(left, right, 0.01, 0) < 1


def test_corrupting_value_cannot_remove_pce_from_the_denominator():
    truth = study()
    prediction = truth.model_copy(deep=True)
    metric = prediction.performance_observations[0].metrics[0]
    metric.raw_value, metric.value_number, metric.unit = "not parseable", None, None
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["photovoltaic_metrics"].predicted == 1
    row = next(
        r
        for r in score.diagnostics.facts["prediction"]
        if r.path.endswith("/metrics/0")
    )
    assert row.rule_id == "individual-photovoltaic"
    assert row.value_interpretation == "literal"
    assert row.context_available
    assert row.blockers == ["wrong_value"]


def test_unverified_citation_ids_and_shared_paragraphs_never_supply_identity():
    truth = study()
    prediction = truth.model_copy(deep=True)
    for record in prediction.performance_observations:
        record.evidence[0].block_id = "same-id-from-another-document"
        record.evidence[0].quote = "Two specimens gave 20% and 23%."
    score = evaluate_study(truth, prediction).core_facts
    assert score.micro.f1 == 1  # Existing structured identity is unchanged.
    assert score.diagnostics.source_anchor_status == "unavailable"
    prediction.individual_devices.append(
        prediction.individual_devices[0].model_copy(
            deep=True, update={"device_id": "second"}
        )
    )
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["photovoltaic_metrics"].matched == 0
    assert "ambiguous_owner" in score.diagnostics.coverage["prediction"].blocker_counts


def test_candidate_resource_limit_is_an_explicit_failure(monkeypatch):
    monkeypatch.setattr(
        "perla_extract.study_extraction.evaluation.MAX_FACT_CANDIDATES", 0
    )
    with pytest.raises(ValueError, match="no truncated score"):
        evaluate_study(study(), study())


def test_canonical_spin_speed_uses_the_same_local_requirements_as_its_alias():
    from tests.study_extraction.test_scoring_structure import recipe

    truth = recipe(["spin coating"])
    step = truth.device_families[0].processing_steps[0]
    step.materials = ["toluene"]
    step.conditions = [quantity("spin-coating speed", 3000, "rpm")]
    prediction = truth.model_copy(deep=True)
    step = prediction.device_families[0].processing_steps[0]
    step.materials = []
    step.conditions[0].name = "speed"
    score = evaluate_study(truth, prediction).core_facts
    assert score.subfields["processing_conditions"].matched == 1
    assert score.subfields["processing_materials"].matched == 0
    step.operation = "blade coating"
    assert (
        evaluate_study(truth, prediction)
        .core_facts.subfields["processing_conditions"]
        .matched
        == 0
    )


def test_pure_text_and_quantity_caches_do_not_change_decisions(monkeypatch):
    from perla_extract.study_extraction import scoring_claims, scoring_context

    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations[0].metrics[0] = quantity(
        "PCE", 0.2, "dimensionless"
    )
    cached = evaluate_study(truth, prediction)
    monkeypatch.setattr(
        scoring_claims,
        "_normalize_fields",
        scoring_claims._normalize_fields.__wrapped__,
    )
    monkeypatch.setattr(
        scoring_context,
        "scientific_value_text",
        scoring_claims.scientific_value_text.__wrapped__,
    )
    monkeypatch.setattr(
        scoring_claims, "scientific_text", scoring_claims.scientific_text.__wrapped__
    )
    assert cached == evaluate_study(truth, prediction)
