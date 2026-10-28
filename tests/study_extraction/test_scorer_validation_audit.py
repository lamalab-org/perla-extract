"""Keep the validation oracle independent of the score it is checking."""

import pytest

from examples.scoring.equivalence_probe import CASES
from examples.scoring.run import quantity, seed_study
from examples.scoring.validate import controls, convert_units, validate_control
from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.models import StudyExtraction
from perla_extract.study_extraction.scoring_facts import equal_value


def test_known_dimensional_arithmetic_preserves_values():
    study = seed_study()
    assert convert_units(study) == 1
    metric = study.performance_observations[0].metrics[0]
    assert metric.value_number == pytest.approx(0.21)
    assert metric.unit == "dimensionless"


def test_qualified_quantity_is_not_reduced_to_a_point():
    study = seed_study()
    study.performance_observations[0].metrics[0].raw_value = ">21%"
    assert convert_units(study) == 0


def test_controls_have_explicit_expected_credit_on_simple_records():
    truth = seed_study()
    truth.performance_observations[0].metrics.append(quantity("FF", 80))
    baseline = evaluate_study(truth, truth)
    rows = [
        validate_control(truth, name, prediction, changes, baseline)
        for name, prediction, changes in controls(truth)
    ]
    assert {r["case"] for r in rows} == {
        "equivalent_units",
        "drop_one_metric",
        "wrong_outcomes_same_identity",
    }
    assert all(r["passed"] for r in rows)


def test_oracle_does_not_accept_unrelated_changes_as_an_equivalent_conversion():
    truth = seed_study()
    changed = truth.model_copy(deep=True)
    changed.performance_observations[0].metrics[0] = quantity("PCE", 80)
    result = validate_control(
        truth, "equivalent_units", changed, 1, evaluate_study(truth, truth)
    )
    assert not result["passed"]


def test_nonunique_protocol_does_not_claim_known_record_identity():
    truth = seed_study()
    second = truth.performance_observations[0].model_copy(deep=True)
    second.observation_id = "second"
    truth.performance_observations.append(second)
    assert "wrong_outcomes_same_identity" not in {
        name for name, _, _ in controls(truth)
    }


@pytest.mark.parametrize(
    "name,left,right,expected",
    [
        pytest.param(
            name,
            left,
            right,
            expected,
            marks=pytest.mark.xfail(
                strict=True,
                reason="Documented conservative comparator does not yet recognize this scientific equivalence",
            )
            if name in {"range_conversion", "uncertainty_conversion", "latex_formula"}
            else (),
            id=name,
        )
        for name, left, right, expected in CASES
    ],
)
def test_independently_specified_scientific_equivalences(name, left, right, expected):
    from perla_extract.study_extraction.models import ReportedValue

    values = [
        ReportedValue(
            name=name,
            raw_value=raw,
            value_number=number,
            unit=unit,
            evidence=[{"block_id": "synthetic", "quote": "Synthetic test only"}],
        )
        for raw, number, unit in (left, right)
    ]
    assert equal_value(*values, relative=1e-6, absolute=0) is expected


@pytest.mark.xfail(
    strict=True,
    reason="Cohort display clarification incorrectly prevents all population matches; identity needs a general solution",
)
def test_clarifying_a_population_label_preserves_unchanged_scientific_facts():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "data/study_extraction/ground_truth/v2/dev/10.1021--acs.chemmater.8b01521/ground_truth.json"
    )
    truth = StudyExtraction.model_validate_json(path.read_text(encoding="utf-8"))
    prediction = truth.model_copy(deep=True)
    for population in prediction.population_statistics:
        # A regression fixture, not a proposed normalization rule. Removing this
        # explanatory clause preserves the same source-defined cohort and values.
        population.label = population.label.removesuffix(
            "; photovoltaic parameter comparison, not a pooled mean"
        )
    report = evaluate_study(truth, prediction)
    assert report.core_facts.groups["population"].matched == 11
    assert report.core_facts.micro.f1 == 1


@pytest.fixture
def one_paper_release(tmp_path):
    import json
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "data/study_extraction/ground_truth/v2"
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    paper = release["papers"][0]
    shutil.copytree(root / paper["path"], tmp_path / paper["path"])
    release.update(paper_count=1, papers=[paper])
    path = tmp_path / "release.json"
    path.write_text(json.dumps(release), encoding="utf-8")
    return path


def test_audit_is_reproducible_and_never_overwrites(one_paper_release, tmp_path):
    import json

    from click.testing import CliRunner

    from examples.scoring.validate import main

    output = tmp_path / "audit.json"
    args = ["--release", str(one_paper_release), "--output", str(output)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["independent_expert_calibration"] is False
    assert sum(not r["agrees"] for r in report["equivalence_probe"]) == 3
    assert report["code_sha256"] and report["environment"]["pint"]
    original = output.read_bytes()
    assert CliRunner().invoke(main, args).exit_code != 0
    assert output.read_bytes() == original


def test_audit_refuses_changed_reference_manifest(one_paper_release):
    import json

    from examples.scoring.validate import run

    release = json.loads(one_paper_release.read_text(encoding="utf-8"))
    manifest_path = (
        one_paper_release.parent / release["papers"][0]["path"] / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["revision"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest differs"):
        run(one_paper_release, None)


def test_audit_refuses_changed_feedback_snapshot(one_paper_release, tmp_path):
    import json
    from zipfile import ZipFile

    from examples.scoring.validate import run

    archive = tmp_path / "feedback.zip"
    with ZipFile(archive, "w") as stream:
        stream.writestr(
            "feedback.json",
            json.dumps(
                {
                    "ground_truth_reviews": [
                        {
                            "split": "dev",
                            "paper_id": "paper",
                            "review_snapshot": {
                                "study": seed_study().model_dump(mode="json"),
                                "study_sha256": "0" * 64,
                            },
                        }
                    ]
                }
            ),
        )
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        run(one_paper_release, archive)
