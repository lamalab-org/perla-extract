import hashlib
import json

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    EvaluationConfig,
    EvaluationReport,
    _maximum_assignment,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_cli import main
from perla_extract.study_extraction.models import (
    AbsorberComponent,
    DeviceFamily,
    EvidenceCitation,
    IndividualDevice,
    Layer,
    MaterialConstituent,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ProcessingStep,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
    study_schema_sha256,
)

EVIDENCE = [EvidenceCitation(block_id="b", quote="reported")]


def provenance(paper_id: str, split: str = "test") -> BenchmarkProvenance:
    """Build distinct, valid frozen-item identities for aggregation tests."""

    return BenchmarkProvenance(
        paper_id=paper_id,
        split=split,
        ground_truth_sha256=hashlib.sha256(f"truth:{paper_id}".encode()).hexdigest(),
        source_manifest_sha256=hashlib.sha256(
            f"source:{paper_id}".encode()
        ).hexdigest(),
        source_sha256=[hashlib.sha256(f"pdf:{paper_id}".encode()).hexdigest()],
    )


def value(raw: str = "20%", number: float = 20, unit: str = "%") -> ReportedValue:
    return ReportedValue(
        name="PCE",
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


def study(
    *, prefix: str = "truth", pce: ReportedValue | None = None
) -> StudyExtraction:
    family = DeviceFamily(
        family_id=f"{prefix}-family",
        label="control p-i-n device",
        variant="control",
        architecture="ITO/2PACz/perovskite/C60/Ag",
        polarity="p-i-n",
        full_stack_raw="ITO/2PACz/perovskite/C60/Ag",
        layers=[],
        absorbers=[],
        processing_steps=[],
        evidence=EVIDENCE,
    )
    device = IndividualDevice(
        device_id=f"{prefix}-device",
        family_id=family.family_id,
        label="champion control",
        variant="control",
        champion_status="yes",
        selection_basis="champion",
        evidence=EVIDENCE,
    )
    observation = PerformanceObservation(
        observation_id=f"{prefix}-observation",
        device_id=device.device_id,
        measurement_type="jv_scan",
        scan_direction="reverse",
        metrics=[pce or value()],
        evidence=EVIDENCE,
    )
    return StudyExtraction(
        paper=PaperMetadata(title="Paper", doi="10.1/example"),
        device_families=[family],
        individual_devices=[device],
        performance_observations=[observation],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def test_identical_science_with_different_ids_scores_perfectly():
    """Run-local identifiers must never reduce evaluation quality."""

    report = evaluate_study(study(prefix="truth"), study(prefix="prediction"))

    assert report.micro_inventory.f1 == 1
    assert report.field_agreement.reported_values.f1 == 1
    assert report.field_agreement.reported_value_accuracy == 1
    assert not report.unmatched_truth_record_keys
    assert not report.unmatched_prediction_record_keys


def test_extra_family_reduces_precision_without_reducing_recall():
    """Over-splitting must be visible as an inventory false positive."""

    truth = study()
    prediction = study(prefix="prediction")
    prediction.device_families.append(
        prediction.device_families[0].model_copy(
            update={"family_id": "extra", "label": "unrelated extra architecture"}
        )
    )

    report = evaluate_study(truth, prediction)

    family_score = report.inventory["device_families"]
    assert family_score.precision == 0.5
    assert family_score.recall == 1
    assert "device_families:extra" in report.unmatched_prediction_record_keys


def test_population_statistic_cannot_match_an_individual_observation():
    """Reporting levels are scored in separate inventories by construction."""

    truth = study()
    truth.population_statistics = [
        PopulationStatistic(
            population_id="population",
            family_id=truth.device_families[0].family_id,
            label="mean of 20 devices",
            statistic_type="mean",
            sample_size=20,
            metrics=[value()],
            evidence=EVIDENCE,
        )
    ]
    prediction = study(prefix="prediction")

    report = evaluate_study(truth, prediction)

    assert report.inventory["population_statistics"].recall == 0
    assert report.inventory["performance_observations"].precision == 1
    assert report.field_agreement.reported_values.recall == 0.5


def test_equivalent_units_are_equal_after_atomic_value_matching():
    truth = study(pce=value("0.20", 0.20, "dimensionless"))
    prediction = study(prefix="prediction", pce=value("20%", 20, "%"))

    report = evaluate_study(truth, prediction)

    assert report.field_agreement.reported_value_accuracy == 1


def test_relative_tolerance_does_not_become_one_absolute_unit_below_one():
    truth = study(pce=value("0.010 M", 0.010, "mol / liter"))
    prediction = study(prefix="prediction", pce=value("0.015 M", 0.015, "mol / liter"))

    report = evaluate_study(truth, prediction)

    assert report.field_agreement.reported_value_accuracy == 0


def test_uncertain_truth_masks_its_matching_prediction():
    """Reviewer abstention must not become either a false positive or false negative."""

    truth = study()
    prediction = study(prefix="prediction")
    report = evaluate_study(
        truth,
        prediction,
        ignored_truth_record_keys=["device_families:truth-family"],
    )

    assert report.inventory["device_families"].predicted == 0
    assert report.inventory["device_families"].truth == 0
    assert report.inventory["device_families"].f1 is None
    assert report.ignored_prediction_record_keys == [
        "device_families:prediction-family"
    ]


def test_parent_relationships_are_scored_separately_from_record_content():
    prediction = study(prefix="prediction")
    prediction.individual_devices[0].family_id = "wrong-family"

    report = evaluate_study(study(), prediction)

    assert report.inventory["individual_devices"].recall == 1
    assert report.field_agreement.relationships_compared > 0
    assert report.field_agreement.relationship_accuracy < 1


def test_reordering_schema_lists_does_not_change_scalar_agreement():
    """Layer order in JSON must not become an error when sequence retains the science."""

    truth = study()
    truth.device_families[0].layers = [
        Layer(
            layer_id="front",
            sequence=1,
            role="transparent_electrode",
            material="ITO",
            material_form="not_reported",
            reported_properties=[],
            evidence=EVIDENCE,
        ),
        Layer(
            layer_id="back",
            sequence=2,
            role="back_electrode",
            material="Ag",
            material_form="not_reported",
            reported_properties=[],
            evidence=EVIDENCE,
        ),
    ]
    prediction = study(prefix="prediction")
    prediction.device_families[0].layers = list(
        reversed(
            [
                layer.model_copy(update={"layer_id": f"prediction-{layer.layer_id}"})
                for layer in truth.device_families[0].layers
            ]
        )
    )

    report = evaluate_study(truth, prediction)

    assert report.field_agreement.scalar_field_accuracy == 1


def test_unknown_uncertainty_mask_key_is_rejected():
    with pytest.raises(ValueError, match="unknown truth records"):
        evaluate_study(
            study(),
            study(prefix="prediction"),
            ignored_truth_record_keys=["device_families:typo"],
        )


def quantity(name, number, unit):
    return ReportedValue(
        name=name,
        raw_value=str(number),
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


def scientific_study():
    """Independent scientific scopes with deliberately repeated property names."""

    result = study()
    family = result.device_families[0]
    family.layers = [
        Layer(
            layer_id="layer",
            sequence=1,
            role="absorber",
            material="MAPbI3",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    ]
    family.absorbers = [
        AbsorberComponent(
            absorber_id="absorber",
            layer_id="layer",
            label="absorber",
            formula=ReportedValue(
                name="formula",
                raw_value="MAPbI3",
                value_number=None,
                unit=None,
                evidence=EVIDENCE,
            ),
            properties=[],
            constituents=[
                MaterialConstituent(
                    name=name,
                    role="precursor",
                    amount=quantity("concentration", n, "mol/liter"),
                    evidence=EVIDENCE,
                )
                for name, n in [("MAI", 0.5), ("PbI2", 1.0)]
            ],
            evidence=EVIDENCE,
        )
    ]
    family.processing_steps = [
        ProcessingStep(
            step_id=f"step-{i}",
            sequence=i,
            operation="annealing",
            target_layer_ids=["layer"],
            materials=["MAPbI3"],
            conditions=[quantity("temperature", t, "°C")],
            evidence=EVIDENCE,
        )
        for i, t in [(1, 100), (2, 150)]
    ]
    result.stability_tests = [
        StabilityTest(
            test_id="stability",
            family_id=family.family_id,
            device_id=None,
            specimen_label="test specimen",
            link_status="explicit_family_link",
            conditions=[quantity("temperature", 65, "°C")],
            checkpoints=[
                StabilityCheckpoint(
                    checkpoint_id=f"time-{t}",
                    time=quantity("time", t, "hour"),
                    conditions=[],
                    outcomes=[quantity("retained PCE", pce, "%")],
                    evidence=EVIDENCE,
                )
                for t, pce in [(100, 95), (500, 80)]
            ],
            evidence=EVIDENCE,
        )
    ]
    return result


def test_core_scores_identical_science_with_reordered_nested_lists():
    truth = scientific_study()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps.reverse()
    prediction.device_families[0].absorbers[0].constituents.reverse()
    prediction.stability_tests[0].checkpoints.reverse()
    report = evaluate_study(truth, prediction)
    assert report.core_facts.micro.f1 == 1
    assert report.core_facts.macro_f1 == 1
    assert not report.core_facts.unmatched_truth_paths


def test_core_wrong_value_is_both_false_positive_and_false_negative():
    report = evaluate_study(
        study(), study(prefix="prediction", pce=value("21%", 21, "%"))
    )
    assert (
        report.field_agreement.reported_values.f1 == 1
    )  # Diagnostic presence, not correctness.
    assert report.core_facts.groups["performance"].model_dump() == {
        "predicted": 1,
        "truth": 1,
        "matched": 0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }
    assert (
        "/performance_observations/0/metrics/0"
        in report.core_facts.unmatched_prediction_paths
    )


@pytest.mark.parametrize(
    "kind", ["step_temperature", "precursor_concentration", "stability_outcome"]
)
def test_swapping_equal_named_values_between_contexts_loses_credit(kind):
    truth = scientific_study()
    prediction = truth.model_copy(deep=True)
    if kind == "step_temperature":
        a, b = prediction.device_families[0].processing_steps
        a.conditions, b.conditions = b.conditions, a.conditions
        expected_paths = [
            f"/device_families/0/processing_steps/{i}/conditions/0" for i in range(2)
        ]
    elif kind == "precursor_concentration":
        a, b = prediction.device_families[0].absorbers[0].constituents
        a.amount, b.amount = b.amount, a.amount
        expected_paths = [
            f"/device_families/0/absorbers/0/constituents/{i}/amount" for i in range(2)
        ]
    else:
        a, b = prediction.stability_tests[0].checkpoints
        a.outcomes, b.outcomes = b.outcomes, a.outcomes
        expected_paths = [
            f"/stability_tests/0/checkpoints/{i}/outcomes/0" for i in range(2)
        ]
    report = evaluate_study(truth, prediction)
    assert set(expected_paths) <= set(report.core_facts.unmatched_truth_paths)
    assert report.core_facts.micro.f1 < 1


@pytest.mark.parametrize("change", ["scan", "champion", "device", "family"])
def test_correct_performance_requires_correct_protocol_and_device_context(change):
    truth = study()
    prediction = study(prefix="prediction")
    if change == "scan":
        prediction.performance_observations[0].scan_direction = "forward"
    elif change == "champion":
        prediction.individual_devices[0].champion_status = "no"
        prediction.individual_devices[0].selection_basis = "representative"
    elif change == "device":
        prediction.performance_observations[0].device_id = "unmatched"
    else:
        prediction.individual_devices[0].family_id = None
    report = evaluate_study(truth, prediction)
    assert report.core_facts.groups["performance"].matched == 0


def test_population_mean_and_maximum_cannot_share_metric_credit():
    truth = study()
    truth.population_statistics = [
        PopulationStatistic(
            population_id="population",
            family_id=truth.device_families[0].family_id,
            label="cohort",
            statistic_type="mean",
            sample_size=20,
            metrics=[value()],
            evidence=EVIDENCE,
        )
    ]
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].statistic_type = "maximum"
    assert (
        evaluate_study(truth, prediction).core_facts.groups["population"].matched == 0
    )


@pytest.mark.parametrize(
    "raw,number,unit",
    [
        (">20%", 20, "%"),
        ("~20%", 20, "%"),
        ("20 ± 2%", 20, "%"),
        ("20–22%", 20, "%"),
        ("20", 20, None),
        ("20 V", 20, "V"),
    ],
)
def test_qualifiers_and_units_are_not_erased(raw, number, unit):
    report = evaluate_study(study(), study(pce=value(raw, number, unit)))
    assert report.core_facts.groups["performance"].matched == 0


@pytest.mark.parametrize("unit", ["percent", "%", "percentage"])
def test_fraction_percent_equivalence_is_symmetric(unit):
    a = study(pce=value("0.20", 0.2, "dimensionless"))
    b = study(pce=value("20", 20, unit))
    assert evaluate_study(a, b).core_facts.groups["performance"].f1 == 1
    assert evaluate_study(b, a).core_facts.groups["performance"].f1 == 1


def test_formula_comparison_preserves_chemical_case():
    truth = scientific_study()
    truth.device_families[0].absorbers[0].formula.raw_value = "CoO"
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].formula.raw_value = "COO"
    report = evaluate_study(truth, prediction)
    assert (
        "/device_families/0/absorbers/0/formula"
        in report.core_facts.unmatched_truth_paths
    )


def test_layer_material_role_pairing_is_not_a_bag_of_words():
    truth = scientific_study()
    truth.device_families[0].layers.append(
        Layer(
            layer_id="second",
            sequence=2,
            material="C60",
            role="electron_transport_layer",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    )
    prediction = truth.model_copy(deep=True)
    a, b = prediction.device_families[0].layers
    a.role, b.role = b.role, a.role
    report = evaluate_study(truth, prediction)
    assert {f"/device_families/0/layers/{i}/role" for i in range(2)} <= set(
        report.core_facts.unmatched_truth_paths
    )


def test_missing_and_duplicate_measurements_affect_recall_and_precision():
    truth = study()
    missing = study(prefix="prediction")
    missing.performance_observations = []
    assert evaluate_study(truth, missing).core_facts.groups["performance"].recall == 0
    duplicate = study(prefix="prediction")
    duplicate.performance_observations.append(
        duplicate.performance_observations[0].model_copy(
            update={"observation_id": "extra"}
        )
    )
    report = evaluate_study(truth, duplicate)
    assert report.core_facts.value_only.groups["performance"].precision == 0.5
    assert report.core_facts.groups["performance"].precision == 0
    assert report.core_facts.scoring_status == "needs_review"
    assert report.inventory["performance_observations"].precision == 0.5


def test_core_mask_does_not_turn_unknown_parent_into_an_attribution_error():
    report = evaluate_study(
        study(),
        study(prefix="prediction"),
        ignored_truth_record_keys=["device_families:truth-family"],
    )
    assert report.core_facts.groups["stack"].f1 is None
    assert report.core_facts.groups["performance"].f1 == 1


def test_core_dataset_report_preserves_counts_and_groups():
    reports = [
        evaluate_study(study(), study(), benchmark=provenance("a")),
        evaluate_study(
            study(), study(pce=value("25%", 25, "%")), benchmark=provenance("b")
        ),
    ]
    result = aggregate_evaluations(reports, bootstrap_samples=50)
    assert result.core_fact_groups_micro["performance"].precision == 0.5
    assert result.core_fact_groups_macro_f1["performance"].mean == 0.5
    assert result.core_fact_groups_micro["stability"].f1 is None
    assert result.core_facts_macro_f1.paper_count == 2
    assert result.core_value_only_micro == result.core_facts_micro
    assert result.core_attribution_micro.f1 == 1
    assert result.papers_needing_scoring_review == 0
    assert result.scoring_issue_count == 0


def test_wrong_value_is_separate_from_correct_attribution():
    score = evaluate_study(study(), study(pce=value("21%", 21))).core_facts
    assert score.groups["performance"].f1 == 0
    assert score.value_only.groups["performance"].f1 == 0
    assert score.attribution.groups["performance"].f1 == 1
    assert score.scoring_status == "ready"


def test_missing_stability_condition_does_not_hide_recovered_values():
    truth = scientific_study()
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].conditions = []
    score = evaluate_study(truth, prediction).core_facts
    assert score.groups["stability"].matched == 0
    assert score.value_only.groups["stability"].matched == 4
    assert score.value_only.groups["stability"].f1 == pytest.approx(8 / 9)
    assert (
        score.groups["stability"].truth
        == score.value_only.groups["stability"].truth
        == 5
    )
    assert score.scoring_status == "ready"


def test_unsequenced_identical_operations_require_attribution_review():
    truth = scientific_study()
    for step in truth.device_families[0].processing_steps:
        step.sequence = None
    prediction = truth.model_copy(deep=True)
    first, second = prediction.device_families[0].processing_steps
    first.conditions, second.conditions = second.conditions, first.conditions
    report = evaluate_study(truth, prediction)
    score = report.core_facts
    assert score.scoring_status == "needs_review"
    assert score.value_only.groups["processing"].f1 == 1
    assert score.groups["processing"].matched == 0
    assert score.groups["processing"].truth == 6
    assert {i.side for i in score.issues} == {"truth", "prediction"}
    assert all(i.kind == "ambiguous_scope" for i in score.issues)
    assert EvaluationReport.model_validate_json(report.model_dump_json()) == report


def test_operation_aliases_are_explicit_and_frozen_in_reports():
    truth = scientific_study()
    prediction = truth.model_copy(deep=True)
    for step in prediction.device_families[0].processing_steps:
        step.operation = "Thermal  annealing"
    assert evaluate_study(truth, prediction).core_facts.groups["processing"].f1 == 1
    literal = evaluate_study(
        truth, prediction, config=EvaluationConfig(operation_aliases={})
    )
    assert literal.core_facts.groups["processing"].f1 == 0
    with pytest.raises(ValueError, match="incompatible"):
        aggregate_evaluations([literal, evaluate_study(truth, prediction)])


@pytest.mark.parametrize(
    "aliases",
    [
        {"": "annealing"},
        {"a": "b", "b": "c"},
        {"a": "b", "b": "a"},
        {"Annealing": "heat", "annealing": "bake"},
    ],
)
def test_ambiguous_or_chained_alias_config_is_rejected(aliases):
    with pytest.raises(ValueError, match="operation aliases"):
        EvaluationConfig(operation_aliases=aliases)


def test_record_ties_propagate_to_linked_measurements():
    truth = study()
    truth.individual_devices.append(
        truth.individual_devices[0].model_copy(update={"device_id": "other"})
    )
    prediction = truth.model_copy(deep=True)
    report = evaluate_study(truth, prediction)
    assert any(m.ambiguous for m in report.matches if m.kind == "individual_devices")
    assert report.core_facts.groups["performance"].matched == 0
    assert report.core_facts.value_only.groups["performance"].matched == 1
    assert report.core_facts.scoring_status == "needs_review"
    prediction.individual_devices.reverse()
    reordered = evaluate_study(truth, prediction)
    assert reordered.core_facts.micro == report.core_facts.micro


@pytest.mark.parametrize("invalid", [False, True])
def test_cli_reads_frozen_alias_configuration(tmp_path, invalid):
    truth, prediction, output, aliases = (
        tmp_path / name
        for name in ("truth.json", "prediction.json", "score.json", "aliases.json")
    )
    truth.write_text(scientific_study().model_dump_json())
    prediction.write_text(scientific_study().model_dump_json())
    aliases.write_text('{"a":"b", "b":"a"}' if invalid else "{}")
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
            "--operation-aliases",
            str(aliases),
        ],
    )
    assert result.exit_code == (1 if invalid else 0)
    if not invalid:
        assert json.loads(output.read_text())["config"]["operation_aliases"] == {}
    else:
        assert "invalid scoring configuration" in result.output
        assert not output.exists()


def test_cli_and_dataset_gate_write_diagnostics_before_failing(tmp_path):
    from perla_extract.study_extraction.evaluation_dataset_cli import (
        main as dataset_main,
    )

    truth, prediction, output, aggregate = (
        tmp_path / name
        for name in ("truth.json", "prediction.json", "score.json", "aggregate.json")
    )
    ambiguous = scientific_study()
    for step in ambiguous.device_families[0].processing_steps:
        step.sequence = None
    truth.write_text(ambiguous.model_dump_json())
    prediction.write_text(ambiguous.model_dump_json())
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
            "--fail-on-scoring-issues",
        ],
    )
    assert result.exit_code == 1
    assert (
        json.loads(output.read_text())["core_facts"]["scoring_status"] == "needs_review"
    )
    result = CliRunner().invoke(
        dataset_main,
        [
            "--report",
            str(output),
            "--output",
            str(aggregate),
            "--fail-on-scoring-issues",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(aggregate.read_text())
    assert payload["papers_needing_scoring_review"] == 1
    assert payload["scoring_issue_count"] == 2
    assert payload["core_value_only_micro"]["f1"] == 1


def test_standard_metric_alias_is_not_a_missing_fact():
    prediction = study(prefix="prediction")
    prediction.performance_observations[0].metrics[
        0
    ].name = "Power conversion efficiency"
    assert evaluate_study(study(), prediction).core_facts.groups["performance"].f1 == 1


@pytest.mark.parametrize("unit", ["mA/cm2", "mA/cm^2", "mA cm⁻²", "mA cm-2"])
def test_current_density_unit_spellings_are_equivalent(unit):
    a = study(pce=quantity("Jsc", 20, unit))
    b = study(pce=quantity("Jsc", 200, "A/m2"))
    assert evaluate_study(a, b).core_facts.groups["performance"].f1 == 1
    assert evaluate_study(b, a).core_facts.groups["performance"].f1 == 1


def test_identical_observations_on_different_devices_are_matched_by_parent():
    truth = study()
    truth.individual_devices.append(
        truth.individual_devices[0].model_copy(
            update={
                "device_id": "second",
                "label": "treated device",
                "variant": "treated",
            }
        )
    )
    truth.performance_observations.append(
        truth.performance_observations[0].model_copy(
            update={
                "observation_id": "second-observation",
                "device_id": "second",
            }
        )
    )
    prediction = truth.model_copy(deep=True)
    prediction.performance_observations.reverse()
    assert evaluate_study(truth, prediction).core_facts.groups["performance"].f1 == 1
    # Change both reported values, keeping the same bag of numbers, on valid devices.
    truth.performance_observations[1].metrics = [value("25%", 25, "%")]
    prediction.performance_observations[1].metrics = [value("25%", 25, "%")]
    assert (
        evaluate_study(truth, prediction).core_facts.groups["performance"].matched == 0
    )


def test_solvent_as_an_extra_layer_reduces_stack_precision():
    truth = scientific_study()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].layers.append(
        Layer(
            layer_id="solvent",
            sequence=2,
            role="other",
            material="DMF",
            reported_properties=[],
            evidence=EVIDENCE,
        )
    )
    score = evaluate_study(truth, prediction).core_facts.groups["stack"]
    assert score.precision < 1
    assert score.recall == 1


def test_missing_parent_does_not_earn_relationship_credit():
    truth = study()
    prediction = study(prefix="prediction")
    prediction.device_families = []
    prediction.individual_devices[0].family_id = None
    report = evaluate_study(truth, prediction)
    assert (
        report.field_agreement.relationships_agreed
        < report.field_agreement.relationships_compared
    )
    assert report.core_facts.groups["performance"].matched == 0


def test_kelvin_and_celsius_are_equivalent_processing_conditions():
    truth = scientific_study()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].processing_steps[0].conditions = [
        quantity("temperature", 373.15, "K")
    ]
    assert evaluate_study(truth, prediction).core_facts.groups["processing"].f1 == 1


def test_empty_prediction_has_zero_recall_not_a_perfect_score():
    truth = scientific_study()
    prediction = StudyExtraction(
        paper=truth.paper,
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    report = evaluate_study(truth, prediction)
    assert report.core_facts.micro.recall == 0
    assert report.core_facts.micro.precision is None
    assert report.core_facts.macro_f1 == 0


def test_administrative_text_does_not_create_extra_scoring_facts():
    prediction = scientific_study()
    prediction.unresolved_notes = ["A very long administrative note" * 20]
    prediction.paper.title = "Different title spelling"
    report = evaluate_study(scientific_study(), prediction)
    assert report.core_facts.micro.f1 == 1


def test_equal_constituent_amounts_with_different_meanings_are_not_equivalent():
    truth = scientific_study()
    amount = truth.device_families[0].absorbers[0].constituents[0].amount
    amount.name = "mole fraction"
    amount.unit = "dimensionless"
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].absorbers[0].constituents[
        0
    ].amount.name = "mass fraction"
    report = evaluate_study(truth, prediction)
    assert (
        "/device_families/0/absorbers/0/constituents/0/amount"
        in report.core_facts.unmatched_truth_paths
    )


def test_global_assignment_avoids_greedy_order_errors():
    scores = [[0.9, 0.8], [0.85, 0.1]]

    assert sorted(_maximum_assignment(scores)) == [(0, 1), (1, 0)]


@pytest.mark.parametrize("artifact_version", [2, 3])
def test_cli_verifies_frozen_truth_manifest(tmp_path, artifact_version):
    payload = study().model_dump(mode="json")
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    truth = tmp_path / "truth"
    truth.mkdir()
    (truth / "ground_truth.json").write_text(json.dumps(payload), encoding="utf-8")
    (truth / "manifest.json").write_text(
        json.dumps(
            {
                "artifact_format_version": artifact_version,
                "evidence_version": 1,
                "evidence_document_sha256": "a" * 64,
                "study_schema_sha256": study_schema_sha256(),
                "paper_id": "paper-a",
                "split": "test",
                "source_manifest": {"main": "source.pdf"},
                "files": {"ground_truth.json": hashlib.sha256(encoded).hexdigest()},
                "review": {"uncertain_record_keys": []},
            }
        ),
        encoding="utf-8",
    )
    prediction = tmp_path / "prediction.json"
    prediction.write_text(
        study(prefix="prediction").model_dump_json(), encoding="utf-8"
    )
    output = tmp_path / "evaluation.json"

    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    result_payload = json.loads(output.read_text())
    assert result_payload["micro_inventory"]["f1"] == 1
    assert result_payload["benchmark"]["paper_id"] == "paper-a"


def test_cli_attaches_evidence_validation_for_complete_prediction_run(tmp_path):
    truth = tmp_path / "truth.json"
    truth.write_text(study().model_dump_json(), encoding="utf-8")
    prediction = tmp_path / "prediction"
    prediction.mkdir()
    (prediction / "extraction.json").write_text(
        study(prefix="prediction").model_dump_json(), encoding="utf-8"
    )
    (prediction / "document.json").write_text(
        json.dumps(
            {
                "blocks": [
                    {
                        "block_id": "b",
                        "source": "main",
                        "page": 1,
                        "kind": "text",
                        "text": "reported 20%",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (prediction / "report.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "usage": {
                    "live_calls": 2,
                    "cache_hits": 0,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cost": 0.01,
                },
                "budget": {
                    "provider_requests": 2,
                    "cost_tracking_complete": True,
                },
                "elapsed_seconds": 3.5,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "evaluation.json"

    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    validation = json.loads(output.read_text())["prediction_validation"]
    assert validation["status"] == "verified"
    assert validation["issues"] == []
    efficiency = json.loads(output.read_text())["run_efficiency"]
    assert efficiency["total_tokens"] == 120
    assert efficiency["cost_usd"] == 0.01


def test_dataset_aggregation_uses_counts_and_skips_undefined_macro_rates():
    perfect = evaluate_study(study(), study(prefix="prediction"))
    empty = StudyExtraction(
        paper=PaperMetadata(title="Empty", doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    empty_report = evaluate_study(empty, empty)

    aggregate = aggregate_evaluations(
        [perfect, empty_report], bootstrap_samples=100, seed=7
    )

    assert aggregate.paper_count == 2
    assert aggregate.overall_micro.f1 == 1
    assert aggregate.overall_macro_f1.paper_count == 1
    assert aggregate.inventory_macro_f1["stability_tests"].mean is None
    assert aggregate.scalar_field_accuracy_micro.accuracy == 1
    assert aggregate.reported_values_micro.f1 == 1
    assert aggregate.reported_value_accuracy_micro.accuracy == 1
    assert aggregate.prediction_validation.paper_count == 0
    assert aggregate.efficiency.paper_count == 0


def test_dataset_aggregation_rejects_split_leakage_and_duplicate_sources():
    first = evaluate_study(
        study(), study(prefix="one"), benchmark=provenance("paper-a", "dev")
    )
    wrong_split = evaluate_study(
        study(), study(prefix="two"), benchmark=provenance("paper-b", "test")
    )
    duplicate_source = provenance("paper-c", "dev").model_copy(
        update={
            "source_sha256": first.benchmark.source_sha256 if first.benchmark else []
        }
    )
    same_source = evaluate_study(
        study(), study(prefix="three"), benchmark=duplicate_source
    )

    with pytest.raises(ValueError, match="mix benchmark splits"):
        aggregate_evaluations([first, wrong_split])
    with pytest.raises(ValueError, match="duplicate source"):
        aggregate_evaluations([first, same_source])


def test_dataset_aggregation_rejects_mixed_provenance_status():
    verified = evaluate_study(
        study(), study(prefix="verified"), benchmark=provenance("paper-a")
    )
    development = evaluate_study(study(), study(prefix="development"))

    with pytest.raises(ValueError, match="cannot mix provenance"):
        aggregate_evaluations([verified, development])
