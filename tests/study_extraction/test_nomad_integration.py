"""Optional contract test against the heavy, pinned NOMAD plugin installation."""

import os
from importlib.metadata import version

import pytest

from perla_extract.study_extraction.nomad_contract import (
    NOMAD_SCHEMA_VERSION,
    NOMADCell,
    NOMADComposition,
    NOMADLayer,
    NOMADProcessingStep,
    NOMADReactionSolution,
    NOMADSolute,
    NOMADSolvent,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("PERLA_RUN_NOMAD_TESTS") != "1",
    reason="set PERLA_RUN_NOMAD_TESTS=1 with the nomad extra installed",
)


def test_outbound_fields_exist_in_pinned_nomad_schema():
    from perovskite_solar_cell_database.llm_extraction_schema import (
        LLMExtractedPerovskiteSolarCell,
    )

    assert version("perovskite-solar-cell-database") == NOMAD_SCHEMA_VERSION
    target_fields = set(LLMExtractedPerovskiteSolarCell.m_def.all_quantities) | set(
        LLMExtractedPerovskiteSolarCell.m_def.all_sub_sections
    )
    outbound_fields = set(NOMADCell.model_fields) - {"m_def"}
    assert outbound_fields <= target_fields

    payload = NOMADCell(
        pce=20.0,
        perovskite_composition=NOMADComposition(long_form="FAPbI3", formula="FAPbI3"),
        layers=[
            NOMADLayer(
                name="FAPbI3",
                functionality="Absorber",
                deposition=[
                    NOMADProcessingStep(
                        method="Spin-coating",
                        solution=NOMADReactionSolution(
                            solutes=[
                                NOMADSolute(
                                    name="FAI", concentration=1, concentration_unit="M"
                                )
                            ],
                            solvents=[NOMADSolvent(name="DMF")],
                        ),
                    )
                ],
            )
        ],
    ).model_dump(exclude_none=True)
    payload.pop("m_def")
    parsed = LLMExtractedPerovskiteSolarCell.m_from_dict(payload)
    assert parsed.pce == 20.0
    assert parsed.perovskite_composition.long_form == "FAPbI3"
    assert parsed.layers[0].name == "FAPbI3"
    assert parsed.layers[0].deposition[0].solution.solutes[0].name == "FAI"
