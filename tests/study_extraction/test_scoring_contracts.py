"""Independent counterexamples from the scientific scoring audit.

Expected credit comes from the stated experiment, not the matcher's current output.
These fixtures deliberately distinguish identity from outcomes and presentation.
"""

import itertools
import json
import random
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    EvaluationReport,
    _assignment,
    _record_pairs,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_dataset_cli import main as aggregate_cli
from perla_extract.study_extraction.models import (
    IndividualDevice,
    ReportedValue,
    StudyExtraction,
)
from perla_extract.study_extraction.reference_release import ReferenceRelease
from perla_extract.study_extraction.scoring_facts import equal_value, is_plain_number

EVIDENCE = [{"block_id": "synthetic", "quote": "synthetic test fixture"}]


def quantity(name, number, unit, raw=None):
    return dict(
        name=name,
        raw_value=str(number) if raw is None else raw,
        value_number=float(number),
        unit=unit,
        evidence=EVIDENCE,
    )


def study():
    """Two treatment cohorts share a design, not their measured outcomes."""
    return StudyExtraction.model_validate(
        {
            "paper": {"title": "Synthetic audit fixture", "doi": None},
            "device_families": [
                {
                    "family_id": "family",
                    "label": "one architecture",
                    "variant": None,
                    "architecture": "ITO/perovskite/Ag",
                    "polarity": "p-i-n",
                    "full_stack_raw": "ITO/perovskite/Ag",
                    "layers": [],
                    "absorbers": [],
                    "processing_steps": [],
                    "evidence": EVIDENCE,
                }
            ],
            "individual_devices": [],
            "performance_observations": [],
            "population_statistics": [
                {
                    "population_id": label,
                    "family_id": "family",
                    "label": label,
                    "statistic_type": "mean",
                    "sample_size": 20,
                    "metrics": [
                        quantity(n, v, u)
                        for n, v, u in zip(
                            ["PCE", "Voc", "Jsc", "FF"],
                            values,
                            ["%", "V", "mA/cm2", "%"],
                        )
                    ],
                    "evidence": EVIDENCE,
                }
                for label, values in [
                    ("control", [18, 1, 23, 78]),
                    ("treated", [22, 1.2, 25, 85]),
                ]
            ],
            "stability_tests": [
                {
                    "test_id": "aging",
                    "family_id": "family",
                    "device_id": None,
                    "specimen_label": "control",
                    "link_status": "explicit_family_link",
                    "conditions": [quantity("temperature", 65, "°C")],
                    "checkpoints": [
                        {
                            "checkpoint_id": "end",
                            "time": quantity("time", 100, "h"),
                            "conditions": [],
                            "outcomes": [quantity("retained PCE", 80, "%")],
                            "evidence": EVIDENCE,
                        }
                    ],
                    "evidence": EVIDENCE,
                }
            ],
            "unresolved_notes": [],
        }
    )


def test_cohort_outcomes_cannot_move_between_control_and_treatment():
    truth = study()
    prediction = truth.model_copy(deep=True)
    a, b = prediction.population_statistics
    a.label, b.label = b.label, a.label
    result = evaluate_study(truth, prediction)
    # Both cohorts are present, and their two sample counts are still correct.
    # None of the eight metrics is assigned to the right treatment arm.
    assert result.inventory["population_statistics"].matched == 2
    assert result.core_facts.groups["population"].matched == 2
    assert result.core_facts.groups["population"].truth == 10
    assert not result.core_facts.issues


def test_stability_control_and_treatment_are_not_the_same_specimen():
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].specimen_label = "treated"
    assert evaluate_study(truth, prediction).core_facts.groups["stability"].matched == 0


def test_descriptor_typography_and_other_display_labels_do_not_change_identity():
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.device_families[0].label = "A different display heading"
    prediction.population_statistics[0].label = " control  "
    prediction.stability_tests[0].specimen_label = " control  "
    assert evaluate_study(truth, prediction).core_facts.micro.f1 == 1


def test_explicit_stability_device_link_takes_precedence_over_display_label():
    truth = study()
    truth.individual_devices = [
        IndividualDevice(
            device_id="cell",
            family_id="family",
            label="control cell",
            variant=None,
            champion_status="not_reported",
            selection_basis="not_reported",
            evidence=EVIDENCE,
        )
    ]
    truth.stability_tests[0].device_id = "cell"
    truth.stability_tests[0].link_status = "explicit_device_link"
    prediction = truth.model_copy(deep=True)
    prediction.stability_tests[0].specimen_label = "A display heading"
    assert evaluate_study(truth, prediction).core_facts.groups["stability"].f1 == 1


def test_unknown_cohort_paraphrase_is_not_guessed_to_be_equivalent():
    truth = study()
    prediction = truth.model_copy(deep=True)
    prediction.population_statistics[0].label = "untreated cells"
    result = evaluate_study(truth, prediction)
    assert result.core_facts.groups["population"].matched == 5
    assert result.core_facts.groups["population"].truth == 10


def test_wrong_stability_outcome_loses_credit_even_with_correct_identity():
    truth = study()
    prediction = truth.model_copy(deep=True)
    outcome = prediction.stability_tests[0].checkpoints[0].outcomes[0]
    outcome.raw_value, outcome.value_number = "85", 85.0
    result = evaluate_study(truth, prediction)
    assert result.core_facts.groups["stability"].matched == 2
    assert result.core_facts.groups["stability"].truth == 3


@pytest.mark.parametrize(
    "left,right",
    [
        (quantity("time", 1, "hour", "1 h"), quantity("time", 3600, "s")),
        (
            quantity("temperature", 65, "°C", "65 ° C"),
            quantity("temperature", 338.15, "K"),
        ),
        (quantity("Jsc", 20, "mA/cm2", "20 mA cm−2"), quantity("Jsc", 200, "A/m2")),
    ],
)
def test_equivalent_unit_suffix_preserves_numeric_equality(left, right):
    a, b = ReportedValue.model_validate(left), ReportedValue.model_validate(right)
    assert equal_value(a, b, 1e-6, 0)
    assert equal_value(b, a, 1e-6, 0)


@pytest.mark.parametrize(
    "raw,unit",
    [
        ("1 h", "s"),
        ("1 nm", "m"),
        ("1 °C", "K"),
        ("1 mV", "V"),
        (">1 h", "hour"),
        ("1 ± 0.2 h", "hour"),
        ("1–2 h", "hour"),
        ("1 h approximately", "hour"),
    ],
)
def test_suffix_equivalence_does_not_relax_scales_offsets_or_qualifiers(raw, unit):
    value = ReportedValue.model_validate(quantity("time", 1, unit, raw))
    assert not is_plain_number(value)


@pytest.mark.parametrize(
    "weights,expected",
    [
        ([[0.875, 0.75], [0.625, 0.5]], True),
        ([[0.9, 0.9], [0.85, 0.4]], False),
    ],
)
def test_global_not_local_assignment_ambiguity(weights, expected):
    with patch(
        "perla_extract.study_extraction.evaluation._record_similarity",
        side_effect=lambda a, b: weights[a][b],
    ):
        pairs = _record_pairs("device_families", [0, 1], [0, 1], [], 0.35)
    assert any(ambiguous for _, _, _, ambiguous in pairs) is expected


def test_global_ambiguity_matches_exhaustive_optimal_assignments():
    """Independently enumerate optima, including dummy edges in rectangular cases."""
    rng = random.Random(1794)
    for rows in range(1, 5):
        for columns in range(1, 5):
            size = max(rows, columns)
            for _ in range(25):
                scores = [
                    [rng.randrange(6) / 4 for _ in range(columns)] for _ in range(rows)
                ]
                orders = list(itertools.permutations(range(size)))
                totals = [
                    sum(
                        scores[i][j]
                        for i, j in enumerate(order)
                        if i < rows and j < columns
                    )
                    for order in orders
                ]
                optima = [
                    order
                    for order, total in zip(orders, totals)
                    if total == max(totals)
                ]
                pairs, ambiguous = _assignment(scores, detect_ambiguity=True)
                assert sum(scores[i][j] for i, j in pairs) == max(totals)
                assert ambiguous == {
                    (i, j) for i, j in pairs if any(order[i] != j for order in optima)
                }


@pytest.mark.parametrize(
    "damage", ["macro", "micro", "matches", "paths", "groups", "views", "status"]
)
def test_loaded_fact_report_must_be_internally_consistent(damage):
    payload = evaluate_study(study(), study()).model_dump(mode="json")
    facts = payload["core_facts"]
    if damage == "macro":
        facts["macro_f1"] = 0.0
    elif damage == "micro":
        facts["micro"] = dict(
            predicted=1, truth=1, matched=1, precision=1.0, recall=1.0, f1=1.0
        )
    elif damage == "matches":
        facts["matches"].pop()
    elif damage == "paths":
        facts["matches"][1]["truth_path"] = facts["matches"][0]["truth_path"]
    elif damage == "groups":
        del facts["groups"]["processing"]
    elif damage == "views":
        facts["value_only"] = evaluate_study(
            study(), study().model_copy(update={"population_statistics": []})
        ).core_facts.value_only.model_dump(mode="json")
    else:
        facts["scoring_status"] = "needs_review"
    with pytest.raises(ValueError):
        EvaluationReport.model_validate(payload)


def release_and_reports():
    records = []
    for paper_id, digest in [("paper-a", "a" * 64), ("paper-b", "b" * 64)]:
        provenance = BenchmarkProvenance(
            paper_id=paper_id,
            split="dev",
            ground_truth_sha256=digest,
            source_manifest_sha256=digest,
            source_sha256=[digest],
        )
        records.append(evaluate_study(study(), study(), benchmark=provenance))
    release = ReferenceRelease.model_validate(
        dict(
            release="synthetic-v1",
            split="dev",
            paper_count=2,
            matcher_version="historical-version-retained",
            papers=[
                dict(
                    paper_id=r.benchmark.paper_id,
                    ground_truth_sha256=r.benchmark.ground_truth_sha256,
                )
                for r in records
            ],
        )
    )
    return release, records


@pytest.mark.parametrize(
    "damage", ["missing", "extra", "duplicate", "hash", "split", "bare"]
)
def test_fixed_release_rejects_incomplete_or_changed_inputs(damage):
    release, reports = release_and_reports()
    if damage == "missing":
        reports.pop()
    elif damage == "duplicate":
        reports.append(reports[0])
    elif damage == "bare":
        reports[0].benchmark = None
    elif damage == "extra":
        reports[0].benchmark.paper_id = "unexpected"
    elif damage == "hash":
        reports[0].benchmark.ground_truth_sha256 = "c" * 64
    else:
        reports[0].benchmark.split = "test"
    with pytest.raises(ValueError):
        aggregate_evaluations(reports, reference_release=release)


def test_fixed_release_binds_the_aggregate_without_rewriting_historical_versions():
    release, reports = release_and_reports()
    result = aggregate_evaluations(
        reports, reference_release=release, bootstrap_samples=0
    )
    assert result.reference_release_sha256 == release.digest()
    assert len(result.input_reports) == 2
    assert result.input_reports[0].benchmark == reports[0].benchmark
    assert (
        result.input_reports[0].prediction_content_sha256
        == reports[0].prediction_content_sha256
    )
    assert release.model_dump()["matcher_version"] == "historical-version-retained"


@pytest.mark.parametrize("damage", ["count", "duplicate"])
def test_fixed_release_checks_its_own_roster(damage):
    release, _ = release_and_reports()
    payload = release.model_dump(mode="json")
    if damage == "count":
        payload["paper_count"] += 1
    else:
        payload["papers"][1] = payload["papers"][0]
    with pytest.raises(ValueError):
        ReferenceRelease.model_validate(payload)


def test_release_cli_refuses_missing_paper_before_writing_aggregate(tmp_path):
    release, reports = release_and_reports()
    release_path, output = tmp_path / "release.json", tmp_path / "result.json"
    release_path.write_text(release.model_dump_json(), encoding="utf-8")
    paths = [tmp_path / f"{i}.json" for i in range(2)]
    for path, report in zip(paths, reports):
        path.write_text(report.model_dump_json(), encoding="utf-8")
    args = [
        "--release",
        str(release_path),
        "--output",
        str(output),
        "--report",
        str(paths[0]),
    ]
    failed = CliRunner().invoke(aggregate_cli, args)
    assert failed.exit_code != 0 and "missing" in failed.output
    assert not output.exists()
    passed = CliRunner().invoke(aggregate_cli, [*args, "--report", str(paths[1])])
    assert passed.exit_code == 0, passed.output
    assert (
        json.loads(output.read_text(encoding="utf-8"))["reference_release_sha256"]
        == release.digest()
    )
