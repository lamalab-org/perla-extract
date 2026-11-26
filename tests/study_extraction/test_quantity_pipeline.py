"""Scientific expectations across grounding, scoring and scalar export.

These fixtures spell out the source claim. Passing citation grounding alone is
deliberately insufficient: convenience fields and lossy destinations have their
own contracts. No expected physical value comes from the production unit parser.
"""

import json
import socket
from dataclasses import replace

import pytest

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.finalization import (
    remove_unsupported_optional_claims,
)
from perla_extract.study_extraction.models import (
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    PerformanceObservation,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
)
from perla_extract.study_extraction.nomad import (
    _stability_summary,
    to_nomad_with_report,
)
from perla_extract.study_extraction.validation import validate_study


def test_offline_contract_guard_blocks_unmocked_network():
    with pytest.raises(AssertionError, match="must not open network connections"):
        socket.getaddrinfo("example.invalid", 443)


def quantity(raw, number, unit, name="PCE"):
    text = f"The reported {name} was {raw}."
    block = EvidenceBlock(
        block_id="source", source="main", page=1, kind="text", text=text
    )
    value = ReportedValue(
        name=name,
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=[EvidenceCitation(block_id="source", quote=text)],
    )
    return value, block


def measured(value):
    return StudyExtraction(
        paper=PaperMetadata(title="Invented quantity contract", doi=None),
        device_families=[],
        individual_devices=[
            IndividualDevice(
                device_id="cell",
                family_id=None,
                label="Cell A",
                variant=None,
                champion_status="not_reported",
                selection_basis="not_reported",
                evidence=value.evidence,
            )
        ],
        performance_observations=[
            PerformanceObservation(
                observation_id="jv",
                device_id="cell",
                measurement_type="jv_scan",
                scan_direction="reverse",
                metrics=[value],
                evidence=value.evidence,
            )
        ],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def test_source_occurrence_cannot_hide_a_numeric_contradiction():
    bad, source = quantity("20%", 2, "%")
    good, _ = quantity("20%", 20, "%")
    prediction = measured(bad)
    original = prediction.model_dump_json()
    validation = validate_study(prediction, [source])
    assert validation["counts"]["source_verified_values"] == 1
    assert validation["counts"]["quantity_interpretations"] == {"contradiction": 1}
    assert [i["code"] for i in validation["issues"]] == ["quantity_conflict"]
    finalized, audit = remove_unsupported_optional_claims(prediction, [source])
    assert finalized == prediction and audit["removal_count"] == 0
    score = evaluate_study(measured(good), prediction)
    for view in (score.core_facts, score.core_facts.recovery):
        field = view.fields["measurement:pce"]
        assert (field.truth, field.predicted, field.matched) == (1, 1, 0)
    exported = to_nomad_with_report(prediction)
    assert exported.archives[0].data.pce is None
    assert "20%" in exported.archives[0].data.additional_notes
    assert prediction.model_dump_json() == original


@pytest.mark.parametrize(
    "raw,number,unit,expected",
    [
        ("<20%", 20, "%", None),
        ("18–20%", 19, "%", None),
        ("20±1%", 20, "%", 20),
        ("∼14 %", 14, "%", 14),
        ("0.2", 0.2, "dimensionless", 20),
        ("0.2", 0.2, None, 0.2),
    ],
)
def test_export_preserves_full_claim_when_the_destination_is_scalar(
    raw, number, unit, expected
):
    value, _ = quantity(raw, number, unit)
    exported = to_nomad_with_report(measured(value))
    cell = exported.archives[0].data
    assert cell.pce == expected
    notes = json.loads(cell.additional_notes)
    assert notes["reported_metrics"][0]["raw_value"] == raw
    if expected is None:
        assert notes["unprojected_metrics"][0]["raw_value"] == raw
    else:
        assert notes["reported_metrics"][0]["interpretation"]["status"] == "normalized"


def test_contradictory_reference_is_flagged_even_when_prediction_repeats_it():
    value, _ = quantity("20%", 2, "%")
    score = evaluate_study(measured(value), measured(value))
    field = score.core_facts.fields["measurement:pce"]
    assert (field.truth, field.predicted, field.matched) == (1, 1, 0)
    assert any(
        i.side == "truth" and i.kind == "quantity_conflict"
        for i in score.core_facts.issues
    )
    assert any(
        "Reference quantity metadata" in message for message in score.input_warnings
    )


def test_malformed_unit_remains_visible_without_aborting_the_paper():
    value, source = quantity("1 V/0", 1, "V/0", "Voc")
    study = measured(value)
    validation = validate_study(study, [source])
    assert validation["counts"]["source_verified_values"] == 1
    assert validation["counts"]["quantity_interpretations"] == {"literal": 1}
    score = evaluate_study(study, study)
    assert score.core_facts.fields["measurement:voc"].truth == 1
    exported = to_nomad_with_report(study)
    assert exported.archives[0].data.voc is None
    assert "V/0" in exported.archives[0].data.additional_notes


def test_finalization_uses_issue_codes_independently_of_display_wording(monkeypatch):
    from perla_extract.study_extraction import finalization
    from tests.study_extraction.test_finalization import (
        _study_with_unsupported_optional_claims,
    )

    study, blocks = _study_with_unsupported_optional_claims()
    actual = finalization.validate_study_details

    def translated(*args):
        result = actual(*args)
        return replace(
            result,
            issues=[
                replace(i, reason="Different explanatory wording")
                for i in result.issues
            ],
        )

    monkeypatch.setattr(finalization, "validate_study_details", translated)
    result, audit = finalization.remove_unsupported_optional_claims(study, blocks)
    assert audit["removal_count"] == 2
    assert result.device_families[0].layers[0].reported_properties == []


def test_conflicting_stability_summaries_are_withheld_independently_of_order():
    value, _ = quantity("20%", 20, "%")

    def q(raw, number, unit, name):
        return quantity(raw, number, unit, name)[0]

    test = StabilityTest(
        test_id="aging",
        family_id=None,
        device_id=None,
        specimen_label="Aging specimen",
        link_status="stability_specimen_only",
        conditions=[
            q("25°C", 25, "°C", "temperature"),
            q("85°C", 85, "°C", "temperature"),
        ],
        checkpoints=[
            StabilityCheckpoint(
                checkpoint_id="start",
                time=q("0 h", 0, "h", "time"),
                outcomes=[value],
                evidence=value.evidence,
            ),
            StabilityCheckpoint(
                checkpoint_id="a",
                time=q("1000 h", 1000, "h", "time"),
                outcomes=[q("10%", 10, "%", "PCE")],
                evidence=value.evidence,
            ),
            StabilityCheckpoint(
                checkpoint_id="b",
                time=q("1000 h", 1000, "h", "time"),
                outcomes=[q("15%", 15, "%", "PCE")],
                evidence=value.evidence,
            ),
        ],
        evidence=value.evidence,
    )
    issues = []
    original = _stability_summary(test, issues)
    assert original.temperature is None and original.PCE_after_1000_hours is None
    assert original.PCE_at_end is None
    assert sum(i.code == "conflicting_stability_summary" for i in issues) == 3
    test.conditions.reverse()
    test.checkpoints[1:] = reversed(test.checkpoints[1:])
    assert _stability_summary(test, []) == original


@pytest.mark.parametrize("unit", ["M", "wt%", "vol%"])
@pytest.mark.parametrize("number,accepted", [(1, True), (99, False)])
def test_concentration_validation_and_projection_share_consistency(
    unit, number, accepted
):
    from perla_extract.study_extraction.enrichment import (
        ProcessingProposalResponse,
        ProcessingProposalResult,
        validate_processing_proposals,
    )
    from perla_extract.study_extraction.nomad import _project_step
    from tests.study_extraction.test_enrichment import (
        processing_proposal,
        study_fixture,
    )

    study = study_fixture()
    step = study.device_families[0].processing_steps[0]
    step.conditions[3] = quantity(f"1 {unit}", number, unit, "FAI concentration")[0]
    proposal = processing_proposal()
    result = validate_processing_proposals(
        study, ProcessingProposalResponse(proposals=[proposal])
    )[0]
    assert (result.status == "accepted") is accepted
    # Even a stale audit marked accepted cannot bypass the export boundary.
    projected = _project_step(
        step, ProcessingProposalResult(proposal=proposal, status="accepted")
    )
    solute = projected.solution.solutes[0]
    assert solute.concentration == (1 if accepted else None)
    assert solute.concentration_unit == (unit if accepted else None)


def test_stability_summary_checks_every_start_and_end_outcome():
    from tests.study_extraction.test_nomad_export import study_fixture

    test = study_fixture().stability_tests[0]
    start = test.checkpoints[0]
    start.time = quantity("0 h", 0, "h", "time")[0]
    start.outcomes = [quantity("20%", 20, "%")[0]]
    other_start = start.model_copy(deep=True, update={"checkpoint_id": "other-start"})
    other_start.outcomes = [quantity("21%", 21, "%")[0]]
    end = start.model_copy(deep=True, update={"checkpoint_id": "end"})
    end.time = quantity("1000 h", 1000, "h", "time")[0]
    end.outcomes = [quantity("17%", 17, "%")[0]]
    other_end = end.model_copy(deep=True, update={"checkpoint_id": "other-end"})
    other_end.outcomes = [quantity(f"{n}%", n, "%")[0] for n in (18, 19)]
    test.checkpoints = [start, other_start, end, other_end]
    issues = []
    summary = _stability_summary(test, issues)
    assert summary.PCE_at_start is None
    assert summary.PCE_at_end is None
    assert summary.PCE_after_1000_hours is None
    assert {i.path for i in issues if i.code == "conflicting_stability_summary"} == {
        "PCE_at_start",
        "PCE_at_end",
        "PCE_after_1000_hours",
    }
    # Equivalent repeated outcomes are safe; a contradictory convenience value
    # cannot disappear in favor of a different checkpoint's valid outcome.
    other_start.outcomes = [quantity("0.2", 0.2, "dimensionless")[0]]
    other_end.outcomes = [quantity("17%", 17, "%")[0]]
    assert _stability_summary(test, []).PCE_at_start == 20
    assert _stability_summary(test, []).PCE_at_end == 17
    other_end.outcomes.append(quantity("17%", 99, "%")[0])
    issues = []
    assert _stability_summary(test, issues).PCE_at_end is None
    assert any(i.code == "unprojectable_stability_summary" for i in issues)
