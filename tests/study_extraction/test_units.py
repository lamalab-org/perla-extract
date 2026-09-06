"""Regression tests for conservative handling of source-reported units."""

from perla_extract.study_extraction.models import EvidenceCitation, ReportedValue
from perla_extract.study_extraction.units import (
    convert_reported_value,
    is_concentration_unit,
)


def _reported_value(unit: str) -> ReportedValue:
    """Build the smallest source-backed value needed to exercise conversion."""

    return ReportedValue(
        name="illumination intensity",
        raw_value=f"70 {unit}",
        value_number=70.0,
        unit=unit,
        evidence=[EvidenceCitation(block_id="main-p1-b1", quote="70 mW cm-2")],
    )


def test_control_character_in_unit_is_unparseable_instead_of_fatal() -> None:
    """Malformed OCR is retained as raw data but must not abort a paper run."""

    malformed_unit = "mW , cm \x00 2"

    assert convert_reported_value(_reported_value(malformed_unit), "watt / meter ** 2") is None
    assert not is_concentration_unit(malformed_unit)
