"""Reference omissions are unknown; explicit contradictions remain errors."""

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.evaluation_cli import main
from perla_extract.study_extraction.models import (
    Layer,
    PopulationStatistic,
    SubjectContext,
)
from tests.study_extraction.test_evaluation import EVIDENCE, quantity, study


def pce_score(truth, prediction):
    return evaluate_study(truth, prediction).core_facts.fields["measurement:pce"]


@pytest.mark.parametrize("field", ["scan_direction", "measurement_type"])
def test_unreported_reference_qualifier_accepts_extra_detail_but_known_one_is_required(
    field,
):
    truth, prediction = study(), study()
    setattr(truth.performance_observations[0], field, "not_reported")
    assert pce_score(truth, prediction).f1 == 1
    assert pce_score(prediction, truth).matched == 0


@pytest.mark.parametrize(
    "field,value",
    [("scan_direction", "forward"), ("measurement_type", "stabilized_power_output")],
)
def test_explicit_measurement_contradiction_is_not_missingness(field, value):
    truth, prediction = study(), study()
    setattr(prediction.performance_observations[0], field, value)
    assert pce_score(truth, prediction).matched == 0


def test_champion_omission_does_not_change_value_for_independently_aligned_specimen():
    truth, prediction = study(), study()
    prediction.individual_devices[0].champion_status = "not_reported"
    assert pce_score(truth, prediction).f1 == 1
    prediction.individual_devices[0].champion_status = "no"
    assert pce_score(truth, prediction).matched == 0


def coded_family():
    result = study()
    family = result.device_families[0]
    family.label, family.variant = "Device 2", "Device 2"
    family.layers = [
        Layer(
            layer_id=str(i),
            sequence=i,
            role="other",
            material=material,
            reported_properties=[],
            evidence=EVIDENCE,
        )
        for i, material in enumerate(["ITO", "TiO2", "MAPbI3", "spiro-OMeTAD", "Au"], 1)
    ]
    return result


def test_explicit_family_designation_can_align_partial_stack_without_certifying_layers():
    truth = coded_family()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].label += " in Table 1"
    prediction.device_families[0].layers = prediction.device_families[0].layers[2:3]
    report = evaluate_study(truth, prediction)
    assert report.core_facts.fields["measurement:pce"].f1 == 1
    pair = next(m for m in report.matches if m.kind == "device_families")
    assert pair.family_features["truth_sample_code"] == ["2"]
    layers = report.core_facts.fields["material:material"]
    assert layers.matched == 1 and layers.truth == 5
    prediction.device_families[0].label = "Device 3"
    prediction.device_families[0].variant = "Device 3"
    assert pce_score(truth, prediction).matched == 0


def cohort():
    result = study()
    result.performance_observations = []
    result.population_statistics = [
        PopulationStatistic(
            population_id="population",
            family_id=result.device_families[0].family_id,
            label="Device 1 population",
            statistic_type="mean",
            measurement_type="jv_scan",
            scan_direction="reverse",
            sample_size=None,
            metrics=[quantity("PCE", 18, "%")],
            evidence=EVIDENCE,
        )
    ]
    return result


def test_cohort_code_survives_label_prose_without_ignoring_statistic_or_subjects():
    truth = cohort()
    prediction = truth.model_copy(deep=True)
    record = prediction.population_statistics[0]
    record.label = "Device 1 average"
    record.subjects = [
        SubjectContext(
            treatment_stage="before_device_completion",
            treated_material=None,
            conditions=[quantity("water concentration", 0, "%")],
        )
    ]
    assert pce_score(truth, prediction).f1 == 1
    record.statistic_type = "maximum"
    assert pce_score(truth, prediction).matched == 0
    record.statistic_type = "mean"
    record.label = "Device 2 average"
    assert pce_score(truth, prediction).matched == 0
    record.label = "Device 1 average"
    truth.population_statistics[0].subjects = [
        SubjectContext(
            treatment_stage="before_device_completion",
            treated_material=None,
            conditions=[quantity("water concentration", 5, "%")],
        )
    ]
    assert pce_score(truth, prediction).matched == 0


@pytest.mark.parametrize(
    "name,number,unit,predicted",
    [
        ("PCE [%]", 20, "%", 20),
        ("FF (%)", 80, "%", 80),
        ("Voc (mV)", 1.1, "V", 1100),
        ("Jsc [mA/cm2]", 22, "mA/cm2", 22),
    ],
)
def test_explicit_units_in_pv_labels_are_read_without_mutating_inputs(
    name, number, unit, predicted
):
    truth, prediction = study(), study()
    base = name.split()[0]
    truth.performance_observations[0].metrics = [quantity(base, number, unit)]
    prediction.performance_observations[0].metrics = [quantity(name, predicted, None)]
    original = prediction.model_dump_json()
    report = evaluate_study(truth, prediction).core_facts
    field = "measurement:" + base.lower()
    assert report.fields[field].f1 == 1
    row = next(
        d
        for d in report.diagnostics.facts["prediction"]
        if d.path.endswith("/metrics/0")
    )
    assert row.unit_source == "field_name"
    assert prediction.model_dump_json() == original


@pytest.mark.parametrize(
    "name,number,unit,raw,source,matched",
    [
        ("PCE", 20, "%", "20", "reported", 1),
        ("PCE", None, None, "20%", "raw_value", 1),
        ("PCE", 20, None, "20", "percentage_points", 1),
        ("PCE [%]", 0.2, None, "0.2", "field_name", 0),
        ("PCE", 20, "V", "20", "reported", 0),
        ("PCE", 20, "unknownunit", "20", "unsupported", 0),
        ("PCE [%]", 20, None, "25%", "unparsed", 0),
        ("PCE [%]", 20, None, "20 V", "unparsed", 0),
    ],
)
def test_unit_evidence_and_contradictions_stay_visible(
    name, number, unit, raw, source, matched
):
    truth, prediction = study(), study()
    metric = quantity(name, number, unit)
    metric.raw_value = raw
    prediction.performance_observations[0].metrics = [metric]
    core = evaluate_study(truth, prediction).core_facts
    assert core.fields["measurement:pce"].matched == matched
    assert core.fields["measurement:pce"].predicted == 1
    diagnostic = next(
        d for d in core.diagnostics.facts["prediction"] if d.path.endswith("/metrics/0")
    )
    assert diagnostic.unit_source == source


def test_cli_exposes_percentage_point_convention(tmp_path):
    truth, prediction = study(), study()
    truth.performance_observations[0].metrics = [quantity("PCE", 20, None)]
    for name, data in (("truth", truth), ("prediction", prediction)):
        (tmp_path / f"{name}.json").write_text(data.model_dump_json())
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(tmp_path / "truth.json"),
            "--prediction",
            str(tmp_path / "prediction.json"),
            "--output",
            str(tmp_path / "evaluation.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        "Unitless PCE/FF treated as percentage points: 1 reference, 0 predicted"
        in result.output
    )


@pytest.mark.parametrize(
    "name",
    [
        "PCE (corrected)",
        "PCE (decimal-corrupted table token)",
        "PCE [unknownunit]",
        "PCE (min)",
        "PCE [V]",
        "PCE (main text)",
        "PCE [%)",
    ],
)
def test_parenthesized_prose_or_unrecognized_units_preserve_the_concept(name):
    truth, prediction = study(), study()
    prediction.performance_observations[0].metrics[0].name = name
    core = evaluate_study(truth, prediction).core_facts
    assert core.fields["measurement:pce"].predicted == 0
    assert core.fields["measurement:pce"].matched == 0


def test_parenthesized_abbreviation_is_not_used_as_a_unit():
    truth, prediction = study(), study()
    metric = prediction.performance_observations[0].metrics[0]
    metric.name, metric.unit = "power conversion efficiency (PCE)", None
    assert pce_score(truth, prediction).f1 == 1  # Unit is still explicit in raw_value.
