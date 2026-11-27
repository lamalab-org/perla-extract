"""Scientific identity and correction controls with independently stated outcomes."""

from copy import deepcopy

import pytest

from perla_extract.study_extraction.correction_checks import (
    _gate_final_candidate_against_draft,
    _select_refinement_candidate,
)
from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.identity_text import identity_text
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    Layer,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
    SubjectContext,
)
from perla_extract.study_extraction.scoring_facts import scientific_descriptor
from perla_extract.study_extraction.scoring_identity import sample_code

TEXT = (
    "Control cell: reverse-scan PCE 15%. Treated cell: reverse-scan PCE 20% "
    "and forward-scan PCE 20%. The material labels Co, CO, Fe³⁺ and Fe₃⁺ "
    "are different scientific statements. The delay was 40 ms or 80 ms."
    " An aging specimen had PCE 20% at 0 h."
)
BLOCKS = [
    EvidenceBlock(block_id="result", source="main", page=1, kind="text", text=TEXT)
]
CITATION = EvidenceCitation(block_id="result", quote=TEXT)


def metric(number=20):
    return ReportedValue(
        name="PCE",
        raw_value=f"{number}%",
        value_number=number,
        unit="%",
        evidence=[CITATION],
    )


def observation(identifier, device, direction="reverse", number=20):
    return PerformanceObservation(
        observation_id=identifier,
        device_id=device,
        measurement_type="jv_scan",
        scan_direction=direction,
        metrics=[metric(number)],
        evidence=[CITATION],
    )


def study(observations):
    return StudyExtraction(
        paper=PaperMetadata(
            title="Synthetic identity and retention controls", doi=None
        ),
        device_families=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
        individual_devices=[
            IndividualDevice(
                device_id=name,
                family_id=None,
                label=name,
                variant=None,
                champion_status="not_reported",
                selection_basis="not_reported",
                evidence=[CITATION],
            )
            for name in ("control", "treated")
        ],
        performance_observations=observations,
    )


@pytest.mark.parametrize(
    "label,expected",
    [
        ("PCE 15%", ""),
        ("PCE 20%", ""),
        ("PCE20%", ""),
        ("efficiency 20%", ""),
        ("η20%", ""),
        ("cell 20% PCE", "cell"),
        ("cell20% PCE", "cell"),
        ("cell25% PCE", "cell"),
        ("Device A20, PCE 20%", "Device A20"),
        ("Device A20 PCE", "Device A20"),
        ("0.2 mg/mL maximum-PCE device", "0.2 mg/mL device"),
        ("c-NiOx specimen, Voc=1.2 V", "c-NiOx specimen"),
        ("Device A20, Jsc22 mA/cm2", "Device A20"),
        ("PCE of about 20%", ""),
        ("PCE approximately 20%", ""),
        ("PCE was measured as 20%", ""),
        ("20% efficient cell", "cell"),
        ("PCE 20% from 10 devices", "from"),
        ("mean, sample size: 10", "mean"),
        ("Device 20, 0.2 mg/mL", "Device 20, 0.2 mg/mL"),
        ("Device 1 PCE distribution", "Device 1 distribution"),
        ("Device1 PCE distribution", "Device1 distribution"),
        ("Device 1 PCE 20%", "Device 1"),
        ("Device 1 PCE20%", "Device 1"),
        ("Device1 PCE20%", "Device1"),
        ("PCE1", ""),
        ("Device PCE1", "Device PCE1"),
    ],
)
def test_identity_projection_excludes_outcomes_but_retains_source_designations(
    label, expected
):
    assert identity_text(label) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Device A20, PCE 20%", "A20"),
        ("Device 1", "1"),
        ("Device 20 from 10 cells", "20"),
        ("Device 1 PCE distribution", "1"),
        ("Device PCE1", "PCE1"),
        ("cell 20% PCE", None),
    ],
)
def test_sample_code_is_read_after_outcome_projection(label, expected):
    device = study([]).individual_devices[0].model_copy(update={"label": label})
    assert sample_code(device) == expected


@pytest.mark.parametrize("field", ["label", "variant"])
@pytest.mark.parametrize(
    "template",
    [
        "PCE {number}%",
        "cell{number}% PCE",
        "PCE of about {number}%",
        "PCE approximately {number}%",
        "PCE was measured as {number}%",
        "{number}% efficient cell",
    ],
)
def test_outcome_text_cannot_resolve_otherwise_ambiguous_device_ownership(
    field, template
):
    fixture = study(
        [observation("a", "control", number=15), observation("b", "treated")]
    )
    for device, number in zip(fixture.individual_devices, (15, 20)):
        device.label = "Measured cell"
        setattr(device, field, template.format(number=number))
    score = evaluate_study(fixture, fixture.model_copy(deep=True))
    assert score.core_facts.recovery.fields["measurement:pce"].matched == 2
    assert score.core_facts.fields["measurement:pce"].matched == 0
    assert all(
        match.ambiguous for match in score.matches if match.kind == "individual_devices"
    )


def test_numbered_population_designations_survive_metric_distribution_labels():
    fixture = study([])
    fixture.population_statistics = [
        PopulationStatistic(
            population_id=f"cohort-{number}",
            family_id=None,
            label=f"Device {number} PCE distribution",
            statistic_type="mean",
            sample_size=None,
            metrics=[metric(15 + number)],
            evidence=[CITATION],
        )
        for number in (1, 2, 3)
    ]
    predicted = fixture.model_copy(deep=True)
    for number, cohort in enumerate(predicted.population_statistics, start=1):
        cohort.label = f"Device {number} population"
    score = evaluate_study(fixture, predicted)
    assert score.core_facts.fields["measurement:pce"].matched == 3
    assert all(
        not match.ambiguous
        for match in score.matches
        if match.kind == "population_statistics"
    )


def test_bare_compact_metric_values_cannot_identify_a_device():
    fixture = study(
        [observation("a", "control", number=15), observation("b", "treated")]
    )
    for device, number in zip(fixture.individual_devices, (15, 20)):
        device.label = f"PCE{number}"
    score = evaluate_study(fixture, fixture.model_copy(deep=True))
    assert score.core_facts.recovery.fields["measurement:pce"].matched == 2
    assert score.core_facts.fields["measurement:pce"].matched == 0


@pytest.mark.parametrize("field", ["label", "variant"])
def test_family_outcome_text_cannot_disambiguate_identical_designs(field):
    fixture = study(
        [observation("a", "control", number=15), observation("b", "treated")]
    )
    for index, (device, number) in enumerate(zip(fixture.individual_devices, (15, 20))):
        device.label = "Measured cell"
        device.family_id = f"family-{index}"
        family = DeviceFamily(
            family_id=device.family_id,
            label="Design",
            variant=None,
            architecture=None,
            full_stack_raw=None,
            polarity="not_reported",
            absorbers=[],
            processing_steps=[],
            layers=[
                Layer(
                    layer_id="electrode",
                    sequence=1,
                    material="ITO",
                    role="transparent_electrode",
                    reported_properties=[],
                    evidence=[CITATION],
                )
            ],
            evidence=[CITATION],
        )
        setattr(family, field, f"PCE {number}%")
        fixture.device_families.append(family)
    score = evaluate_study(fixture, fixture.model_copy(deep=True))
    assert score.core_facts.recovery.fields["measurement:pce"].matched == 2
    assert score.core_facts.fields["measurement:pce"].matched == 0
    assert all(
        match.ambiguous for match in score.matches if match.kind == "device_families"
    )


def test_population_outcome_labels_cannot_establish_cohort_identity():
    fixture = study([])
    fixture.population_statistics = [
        PopulationStatistic(
            population_id=f"cohort-{index}",
            family_id=None,
            label=f"cell{number}% PCE",
            statistic_type="mean",
            sample_size=5,
            metrics=[metric(number)],
            evidence=[CITATION],
        )
        for index, number in enumerate((15, 20))
    ]
    score = evaluate_study(fixture, fixture.model_copy(deep=True))
    assert score.core_facts.recovery.fields["measurement:pce"].matched == 2
    assert score.core_facts.fields["measurement:pce"].matched == 0
    assert all(
        match.ambiguous
        for match in score.matches
        if match.kind == "population_statistics"
    )


@pytest.mark.parametrize(
    "label", ["from {count} devices", "from {count}devices", "sample size: {count}"]
)
def test_population_counts_copied_into_labels_cannot_establish_cohort_identity(label):
    fixture = study([])
    fixture.population_statistics = [
        PopulationStatistic(
            population_id=f"cohort-{index}",
            family_id=None,
            label=f"PCE {number}% {label.format(count=count)}",
            statistic_type="mean",
            sample_size=count,
            metrics=[metric(number)],
            evidence=[CITATION],
        )
        for index, (number, count) in enumerate(((15, 10), (20, 20)))
    ]
    score = evaluate_study(fixture, fixture.model_copy(deep=True))
    for field in ("measurement:pce", "measurement:sample_size"):
        assert score.core_facts.recovery.fields[field].matched == 2
        assert score.core_facts.fields[field].matched == 0
    assert all(
        match.ambiguous
        for match in score.matches
        if match.kind == "population_statistics"
    )


def test_unlinked_stability_specimen_descriptor_uses_the_same_outcome_projection():
    first = StabilityTest(
        test_id="aging",
        family_id=None,
        device_id=None,
        specimen_label="cell20% PCE",
        link_status="stability_specimen_only",
        conditions=[],
        checkpoints=[
            StabilityCheckpoint(
                checkpoint_id="start",
                time=None,
                conditions=[],
                outcomes=[metric()],
                evidence=[CITATION],
            )
        ],
        evidence=[CITATION],
    )
    second = first.model_copy(update={"specimen_label": "cell25% PCE"})
    assert scientific_descriptor(first) == scientific_descriptor(second) == "cell"


def test_distinct_equal_valued_scans_cannot_be_deleted_as_a_duplicate():
    draft = study([observation("r", "treated"), observation("f", "treated", "forward")])
    revised = draft.model_copy(deep=True)
    revised.performance_observations.pop()
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == draft
    assert audit["refinement_quality"]["lost_verified_values"] == 1


@pytest.mark.parametrize("edit", ["label", "citation"])
def test_presentation_edits_keep_the_same_source_supported_claim(edit):
    draft = study([observation("r", "treated")])
    revised = draft.model_copy(deep=True)
    blocks = list(BLOCKS)
    if edit == "label":
        revised.individual_devices[1].label = "Treated cell with clarified label"
    else:
        blocks.append(BLOCKS[0].model_copy(update={"block_id": "other"}))
        revised.performance_observations[0].metrics[0].evidence = [
            CITATION.model_copy(update={"block_id": "other"})
        ]
    selected, audit = _select_refinement_candidate(draft, revised, blocks, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0


@pytest.mark.parametrize("before,after", [("Co", "CO"), ("Fe³⁺", "Fe₃⁺")])
def test_retention_preserves_chemical_case_and_charge(before, after):
    draft = study([])
    draft.individual_devices[0].reported_properties = [
        ReportedValue(
            name="material",
            raw_value=before,
            value_number=None,
            unit=None,
            evidence=[CITATION],
        )
    ]
    revised = draft.model_copy(deep=True)
    revised.individual_devices[0].reported_properties[0].raw_value = after
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == draft
    assert audit["refinement_quality"]["lost_verified_values"] == 1


def test_one_explicit_device_link_edit_retains_the_value_and_is_audited():
    draft = study([observation("r", "control")])
    revised = draft.model_copy(deep=True)
    revised.performance_observations[0].device_id = "treated"
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0
    assert audit["refinement_quality"]["ownership_changes"] == 1


@pytest.mark.parametrize(
    "original,accepted", [("not_reported", True), ("forward", False)]
)
def test_missing_measurement_context_can_be_added_but_explicit_context_is_retained(
    original, accepted
):
    draft = study([observation("r", "treated", original)])
    revised = draft.model_copy(deep=True)
    revised.performance_observations[0].scan_direction = "reverse"
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == (revised if accepted else draft)
    assert audit["refinement_quality"]["context_enrichments"] == (1 if accepted else 0)
    assert audit["refinement_quality"]["lost_verified_values"] == (0 if accepted else 1)


def test_unique_ownership_correction_can_also_recover_a_missing_value():
    draft = study([observation("misattributed", "control")])
    revised = study(
        [
            observation("corrected", "treated"),
            observation("control", "control", number=15),
        ]
    )
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0
    assert audit["refinement_quality"]["ownership_changes"] == 1


def test_exact_duplicate_observation_can_be_removed():
    first = observation("first", "treated")
    draft = study([first, first.model_copy(update={"observation_id": "duplicate"})])
    revised = draft.model_copy(deep=True)
    revised.performance_observations.pop()
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0


def test_exact_duplicate_stability_record_can_use_different_checkpoint_ids():
    checkpoint = StabilityCheckpoint(
        checkpoint_id="point",
        time=ReportedValue(
            name="time", raw_value="0 h", value_number=0, unit="h", evidence=[CITATION]
        ),
        conditions=[],
        outcomes=[metric()],
        evidence=[CITATION],
    )
    first = StabilityTest(
        test_id="first",
        family_id=None,
        device_id=None,
        specimen_label="Aging specimen",
        link_status="stability_specimen_only",
        conditions=[],
        checkpoints=[checkpoint],
        evidence=[CITATION],
    )
    duplicate = first.model_copy(deep=True)
    duplicate.test_id = "duplicate"
    duplicate.checkpoints[0].checkpoint_id = "other-point"
    draft = study([])
    draft.stability_tests = [first, duplicate]
    revised = draft.model_copy(deep=True)
    revised.stability_tests.pop()
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0


def test_ambiguous_reidentified_claims_keep_the_earlier_candidate():
    draft = study([observation("a", "control"), observation("b", "treated")])
    revised = draft.model_copy(deep=True)
    revised.performance_observations[0].observation_id = "new-a"
    revised.performance_observations[1].observation_id = "new-b"
    revised.performance_observations[0].device_id = "treated"
    revised.performance_observations[1].device_id = "control"
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == draft
    assert audit["refinement_quality"]["ambiguous_ownership_edits"] == 2


def test_raw_supported_quantity_survives_correction_of_bad_convenience_metadata():
    draft = study([observation("r", "treated")])
    draft.performance_observations[0].metrics[0].value_number = 2
    revised = draft.model_copy(deep=True)
    revised.performance_observations[0].metrics[0].value_number = 20
    selected, audit = _select_refinement_candidate(draft, revised, BLOCKS, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0


@pytest.mark.parametrize("seconds,accepted", [(1.8, True), (1.81, False)])
def test_retention_allows_conversion_roundoff_but_not_scoring_tolerance(
    seconds, accepted
):
    text = "A duration of 0.03 min is 1.8 s; another reported duration was 1.81 s."
    block = BLOCKS[0].model_copy(update={"text": text})
    citation = EvidenceCitation(block_id="result", quote=text)
    draft = study([])
    for device in draft.individual_devices:
        device.evidence = [citation]
    draft.individual_devices[0].reported_properties = [
        ReportedValue(
            name="duration",
            raw_value="0.03 min",
            value_number=0.03,
            unit="min",
            evidence=[citation],
        )
    ]
    revised = draft.model_copy(deep=True)
    revised.individual_devices[0].reported_properties[0] = ReportedValue(
        name="duration",
        raw_value=f"{seconds:g} s",
        value_number=seconds,
        unit="s",
        evidence=[citation],
    )
    selected, audit = _select_refinement_candidate(draft, revised, [block], None)
    assert selected == (revised if accepted else draft)
    assert audit["refinement_quality"]["lost_verified_values"] == (0 if accepted else 1)


def test_final_integrity_check_does_not_overwrite_proposal_rejection():
    draft = study([observation("r", "treated"), observation("f", "treated", "forward")])
    revised = draft.model_copy(deep=True)
    revised.performance_observations.pop()
    selected, selection = _select_refinement_candidate(draft, revised, BLOCKS, None)
    original = deepcopy(selection)
    final, audit = _gate_final_candidate_against_draft(
        draft, selected, BLOCKS, None, selection
    )
    assert final == draft
    assert audit["proposal_decision"]["selected"] == "draft"
    assert audit["reason"] == original["reason"]
    assert audit["final_integrity_check"]["accepted"]
    assert selection == original


def treatment_arm_fixture():
    """Two source-stated arms share temperature but differ in atmosphere."""

    text = (
        "Film cohorts were treated in air at 80 °C and in nitrogen at 80 °C. "
        "The treatment lasted 10 min. Mean PCE was 20%."
    )
    block = EvidenceBlock(
        block_id="arms", source="main", page=1, kind="text", text=text
    )
    citation = EvidenceCitation(block_id="arms", quote=text)

    def value(name, raw, number=None, unit=None):
        return ReportedValue(
            name=name,
            raw_value=raw,
            value_number=number,
            unit=unit,
            evidence=[citation],
        )

    fixture = study([])
    fixture.individual_devices = []
    fixture.population_statistics = [
        PopulationStatistic(
            population_id="cohort",
            family_id=None,
            label="Treated films",
            statistic_type="mean",
            sample_size=None,
            subjects=[
                SubjectContext(
                    treatment_stage="before_device_completion",
                    treated_material="film",
                    conditions=[
                        value("temperature", "80 °C", 80, "°C"),
                        value("atmosphere", atmosphere),
                    ],
                )
                for atmosphere in ("air", "nitrogen")
            ],
            metrics=[value("PCE", "20%", 20, "%")],
            evidence=[citation],
        )
    ]
    return fixture, [block], value


@pytest.mark.parametrize("shared_temperature", [True, False])
def test_distinct_population_arms_cannot_be_collapsed_into_one(
    shared_temperature,
):
    draft, blocks, _ = treatment_arm_fixture()
    if not shared_temperature:
        for arm in draft.population_statistics[0].subjects:
            arm.conditions.pop(0)
    revised = draft.model_copy(deep=True)
    first, second = revised.population_statistics[0].subjects
    first.conditions.extend(second.conditions)
    revised.population_statistics[0].subjects.pop()
    selected, audit = _select_refinement_candidate(draft, revised, blocks, None)
    assert selected == draft
    assert audit["refinement_quality"]["lost_verified_values"] > 0


def test_repeated_population_arms_retain_their_multiplicity():
    draft, blocks, _ = treatment_arm_fixture()
    subjects = draft.population_statistics[0].subjects
    subjects.append(subjects[0].model_copy(deep=True))
    revised = draft.model_copy(deep=True)
    revised.population_statistics[0].subjects.pop()
    selected, audit = _select_refinement_candidate(draft, revised, blocks, None)
    assert selected == draft
    assert audit["refinement_quality"]["lost_verified_values"] == 2


def test_population_arm_and_condition_reordering_preserves_claims():
    draft, blocks, _ = treatment_arm_fixture()
    revised = draft.model_copy(deep=True)
    revised.population_statistics[0].subjects.reverse()
    for arm in revised.population_statistics[0].subjects:
        arm.conditions.reverse()
    selected, audit = _select_refinement_candidate(draft, revised, blocks, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0
    assert audit["refinement_quality"]["context_enrichments"] == 0


@pytest.mark.parametrize("enrichment", ["condition", "stage", "material"])
def test_uniquely_corresponding_population_arm_can_acquire_missing_context(
    enrichment,
):
    draft, blocks, value = treatment_arm_fixture()
    arm = draft.population_statistics[0].subjects[0]
    if enrichment == "stage":
        arm.treatment_stage = "not_reported"
    elif enrichment == "material":
        arm.treated_material = None
    revised = draft.model_copy(deep=True)
    revised_arm = revised.population_statistics[0].subjects[0]
    if enrichment == "condition":
        revised_arm.conditions.append(value("duration", "10 min", 10, "min"))
    elif enrichment == "stage":
        revised_arm.treatment_stage = "before_device_completion"
    else:
        revised_arm.treated_material = "film"
    selected, audit = _select_refinement_candidate(draft, revised, blocks, None)
    assert selected == revised
    assert audit["refinement_quality"]["lost_verified_values"] == 0
    assert audit["refinement_quality"]["context_enrichments"] == 2
