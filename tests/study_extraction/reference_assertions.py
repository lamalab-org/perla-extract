"""Independent expectations for known dimensional defects in frozen references.

An inverse square centimetre is not an area. Four copies of this exact annotation
exist in the archived Solar RRL reference. They remain unchanged and must be
flagged, counted and denied numerical credit, even in a self-comparison. This is
test data about the archived release, never an exception in production scoring.
"""

from perla_extract.study_extraction.evaluation import EvaluationReport
from perla_extract.study_extraction.models import StudyExtraction


def assert_reference_self_score(
    truth: StudyExtraction, report: EvaluationReport
) -> None:
    expected = {
        f"/individual_devices/{i}/reported_properties/{j}"
        for i, device in enumerate(truth.individual_devices)
        for j, value in enumerate(device.reported_properties)
        if (value.name, value.raw_value, value.value_number, value.unit)
        == ("active area", "0.06 cm -2", 0.06, "cm -2")
    }
    observed = {
        (issue.side, path)
        for issue in report.core_facts.issues
        for path in issue.paths
        if issue.kind == "quantity_conflict"
    }
    assert observed == {
        (side, path) for side in ("truth", "prediction") for path in expected
    }
    assert all(issue.kind == "quantity_conflict" for issue in report.core_facts.issues)
    for view in (report.core_facts, report.core_facts.recovery):
        assert set(view.unmatched_truth_paths) == expected
        assert set(view.unmatched_prediction_paths) == expected
        assert view.micro.truth == view.micro.predicted
        assert view.micro.matched == view.micro.truth - len(expected)
