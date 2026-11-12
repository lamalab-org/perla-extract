"""Frozen reference controls retain scientific credit and explanation transparency.

Expectations come from known edits, not golden hashes of dependency-sensitive
lexical similarity. Complete old/new scorer parity is a separate refactor audit.
"""

from pathlib import Path

import pytest

from examples.scoring.validate import controls, validate_control
from perla_extract.study_extraction.evaluation import (
    evaluate_study,
    evaluate_study_with_explanations,
)
from perla_extract.study_extraction.models import StudyExtraction
from perla_extract.study_extraction.scoring_explanation import FactSelection

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "paper",
    [
        "10.1002--adma.202305822",
        "10.1021--acs.chemmater.8b01521",
        "10.1038--s41467-020-20272-3",
    ],
)
def test_frozen_reference_controls_and_explanations_preserve_credit(paper):
    path = (
        ROOT / "data/study_extraction/ground_truth/v2/dev" / paper / "ground_truth.json"
    )
    truth = StudyExtraction.model_validate_json(path.read_text())
    original = truth.model_dump_json()
    baseline = evaluate_study(truth, truth)
    assert baseline.core_facts.micro.f1 == 1
    assert not baseline.core_facts.issues
    for name, prediction, changes in controls(truth):
        # Equivalent units preserve credit; one removed metric removes one fact;
        # corrupt outcomes lose exactly their value credit without changing identity.
        checked = validate_control(truth, name, prediction, changes, baseline)
        assert checked["passed"], checked["failures"]
        plain = evaluate_study(truth, prediction)
        paths = list(
            dict.fromkeys(
                [
                    *plain.core_facts.unmatched_truth_paths,
                    *(m.truth_path for m in plain.core_facts.matches),
                ]
            )
        )[:3]
        explained, details = evaluate_study_with_explanations(
            truth,
            prediction,
            facts=[FactSelection(side="truth", path=p) for p in paths],
            max_candidates=2,
        )
        assert explained == plain
        assert len(details.facts) == len(paths)
        assert all(len(f.candidates) <= 2 for f in details.facts)
    assert truth.model_dump_json() == original
