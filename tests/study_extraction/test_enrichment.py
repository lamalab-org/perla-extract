from perla_extract.study_extraction.enrichment import (
    CompositionProposal,
    CompositionProposalResponse,
    ProcessingConditionAssignment,
    ProcessingMaterialAssignment,
    ProcessingProposalResponse,
    ProcessingStepProposal,
    ProposedIon,
    composition_context,
    run_enrichment,
    validate_composition_proposals,
    validate_processing_proposals,
)
from perla_extract.study_extraction.models import (
    AbsorberComponent,
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    Layer,
    PaperMetadata,
    ProcessingStep,
    ReportedValue,
    StudyExtraction,
)

CITATION = EvidenceCitation(block_id="b1", quote="FAPbI3 was annealed at 373 K")


def value(name: str, raw: str, number: float | None, unit: str | None) -> ReportedValue:
    return ReportedValue(
        name=name,
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=[CITATION],
    )


def study_fixture() -> StudyExtraction:
    return StudyExtraction(
        paper=PaperMetadata(title="Example", doi=None),
        device_families=[
            DeviceFamily(
                family_id="f1",
                label="control",
                variant=None,
                architecture=None,
                polarity="not_reported",
                full_stack_raw=None,
                layers=[
                    Layer(
                        layer_id="l1",
                        sequence=1,
                        role="absorber",
                        material="FAPbI3",
                        reported_properties=[],
                        evidence=[CITATION],
                    )
                ],
                absorbers=[
                    AbsorberComponent(
                        absorber_id="a1",
                        layer_id="l1",
                        label="FAPbI3 absorber",
                        formula=value("formula", "FAPbI₃", None, None),
                        properties=[],
                        constituents=[],
                        evidence=[CITATION],
                    )
                ],
                processing_steps=[
                    ProcessingStep(
                        step_id="s1",
                        sequence=1,
                        operation="annealing and coating",
                        target_layer_ids=["l1"],
                        materials=["FAI", "DMF", "chlorobenzene"],
                        conditions=[
                            value("annealing value", "373 K", 373, "K"),
                            value("annealing period", "10 min", 10, "min"),
                            value("environment", "under nitrogen", None, None),
                            value("FAI concentration", "1 M", 1, "M"),
                        ],
                        evidence=[CITATION],
                    )
                ],
                evidence=[CITATION],
            )
        ],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def composition_proposal(formula_iodine_coefficient: str = "3") -> CompositionProposal:
    return CompositionProposal(
        family_id="f1",
        absorber_id="a1",
        ions=[
            ProposedIon(site="A", abbreviation="FA", coefficient="1"),
            ProposedIon(site="B", abbreviation="Pb", coefficient="1"),
            ProposedIon(
                site="X", abbreviation="I", coefficient=formula_iodine_coefficient
            ),
        ],
    )


def test_composition_is_accepted_only_when_sites_reconstruct_reported_formula():
    study = study_fixture()

    accepted = validate_composition_proposals(
        study, CompositionProposalResponse(proposals=[composition_proposal()])
    )[0]
    mismatch = validate_composition_proposals(
        study,
        CompositionProposalResponse(proposals=[composition_proposal("2.9")]),
    )[0]

    assert accepted.status == "accepted"
    assert mismatch.status == "needs_review"
    assert "exactly reconstruct" in mismatch.issues[0]


def test_parenthesized_x_site_multiplicity_preserves_fractional_occupancy():
    study = study_fixture()
    study.device_families[0].absorbers[0].formula = value(
        "formula", "Cs0.3FA0.6DMA0.1Pb(I0.7Br0.3)3", None, None
    )
    proposal = CompositionProposal(
        family_id="f1",
        absorber_id="a1",
        ions=[
            ProposedIon(site="A", abbreviation="Cs", coefficient="0.3"),
            ProposedIon(site="A", abbreviation="FA", coefficient="0.6"),
            ProposedIon(site="A", abbreviation="DMA", coefficient="0.1"),
            ProposedIon(site="B", abbreviation="Pb", coefficient="1"),
            ProposedIon(site="X", abbreviation="I", coefficient="0.7"),
            ProposedIon(site="X", abbreviation="Br", coefficient="0.3"),
        ],
    )

    result = validate_composition_proposals(
        study, CompositionProposalResponse(proposals=[proposal])
    )[0]

    assert result.status == "accepted"


def test_composition_context_contains_only_cited_evidence():
    blocks = [
        EvidenceBlock(
            block_id="b1",
            source="main",
            page=1,
            section_path=["Results"],
            kind="paragraph",
            text="FAPbI3 was annealed at 373 K",
        ),
        EvidenceBlock(
            block_id="unrelated",
            source="supplement",
            page=30,
            section_path=["References"],
            kind="paragraph",
            text="Unrelated prior work",
        ),
    ]

    context = composition_context(study_fixture(), blocks)

    assert [item["block_id"] for item in context[0]["evidence"]] == ["b1"]


def test_automatic_acceptance_requires_source_grounding():
    unsupported = EvidenceBlock(
        block_id="b1",
        source="main",
        page=1,
        section_path=[],
        kind="paragraph",
        text="This block does not report the proposed composition.",
    )

    result = validate_composition_proposals(
        study_fixture(),
        CompositionProposalResponse(proposals=[composition_proposal()]),
        [unsupported],
    )[0]

    assert result.status == "needs_review"
    assert "not grounded" in result.issues[0]


def processing_proposal(condition_index: int = 0) -> ProcessingStepProposal:
    return ProcessingStepProposal(
        step_id="s1",
        condition_assignments=[
            ProcessingConditionAssignment(
                condition_index=condition_index,
                target_field="temperature",
                atmosphere=None,
            ),
            ProcessingConditionAssignment(
                condition_index=1,
                target_field="duration",
                atmosphere=None,
            ),
            ProcessingConditionAssignment(
                condition_index=2,
                target_field="atmosphere",
                atmosphere="N2",
            ),
        ],
        material_assignments=[
            ProcessingMaterialAssignment(
                material_index=0,
                role="solute",
                concentration_condition_index=3,
            ),
            ProcessingMaterialAssignment(
                material_index=1,
                role="solvent",
                concentration_condition_index=None,
            ),
            ProcessingMaterialAssignment(
                material_index=2,
                role="antisolvent",
                concentration_condition_index=None,
            ),
        ],
    )


def test_processing_accepts_only_resolvable_atomic_source_pointers():
    study = study_fixture()

    accepted = validate_processing_proposals(
        study, ProcessingProposalResponse(proposals=[processing_proposal()])
    )[0]
    invalid = validate_processing_proposals(
        study, ProcessingProposalResponse(proposals=[processing_proposal(99)])
    )[0]

    assert accepted.status == "accepted"
    assert invalid.status == "needs_review"
    assert "out of range" in invalid.issues[0]


def test_enrichment_retries_only_omitted_compositions_and_reports_them():
    class FakeClient:
        def __init__(self):
            self.kinds: list[str] = []

        def complete(self, **request):
            self.kinds.append(request["kind"])
            if request["kind"].startswith("composition_enrichment"):
                return CompositionProposalResponse(proposals=[])
            return ProcessingProposalResponse(proposals=[])

    client = FakeClient()
    audit = run_enrichment(
        client=client,
        study=study_fixture(),
        blocks=[],
        model="provider/model",
        reasoning_effort=None,
        max_output_tokens=1000,
    )

    assert client.kinds == [
        "composition_enrichment",
        "composition_enrichment_retry",
        "processing_enrichment",
    ]
    assert audit.unresolved_absorber_ids == ["a1"]
    assert audit.unresolved_processing_step_ids == ["s1"]
