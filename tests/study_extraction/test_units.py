"""Regression tests for conservative handling of source-reported units."""

import pytest

from perla_extract.study_extraction.models import EvidenceCitation, ReportedValue
from perla_extract.study_extraction.quantities import convert_reported_value
from perla_extract.study_extraction.units import (
    canonical_quantity,
    equivalent_unit_spelling,
    is_concentration_unit,
)


def _reported_value(unit: str) -> ReportedValue:
    """Build a scalar for unit tests; workflow tests check source grounding."""

    return ReportedValue(
        name="illumination intensity",
        raw_value=f"70 {unit}",
        value_number=70.0,
        unit=unit,
        evidence=[EvidenceCitation(block_id="main-p1-b1", quote=f"70 {unit}")],
    )


def test_control_character_in_unit_is_unparseable_instead_of_fatal() -> None:
    """Malformed OCR is retained as raw data but must not abort a paper run."""

    malformed_unit = "mW , cm \x00 2"

    assert (
        convert_reported_value(_reported_value(malformed_unit), "watt / meter ** 2")
        is None
    )
    assert not is_concentration_unit(malformed_unit)


@pytest.mark.parametrize("unit", ["#", "#count", "mW # comment", "wt%#comment"])
def test_unit_labels_cannot_be_interpreted_as_parser_comments(unit):
    """Unknown labels remain literal; a comment cannot erase part of a unit."""

    assert canonical_quantity(70, unit) is None
    assert convert_reported_value(_reported_value(unit), "mW") is None
    assert convert_reported_value(_reported_value("mW"), unit) is None
    assert convert_reported_value(_reported_value(unit), "percent") is None
    assert not equivalent_unit_spelling(unit, unit)
    assert not is_concentration_unit(unit)


@pytest.mark.parametrize("unit", ["Ω/", "/", "()", "+"])
def test_incomplete_unit_expressions_remain_unparseable(unit):
    assert canonical_quantity(70, unit) is None
    assert convert_reported_value(_reported_value(unit), "ohm") is None
    assert not equivalent_unit_spelling(unit, "ohm")
    assert not is_concentration_unit(unit)


@pytest.mark.parametrize("unit", ["mA/cm^{2}", "mg/ml (~1M)"])
def test_unsupported_notation_has_the_same_result_across_pint_versions(unit):
    """Do not silently strip braces or secondary quantities from a unit label."""

    assert canonical_quantity(1, unit) is None
    assert not equivalent_unit_spelling(unit, "mA/cm2")


@pytest.mark.parametrize("minus", ["\u2212", "\u2012", "\u2013", "\uf02d"])
@pytest.mark.parametrize(
    "printed,standard",
    [
        ("mA cm{minus}2", "mA/cm2"),
        ("mW cm{minus}2", "mW/cm2"),
        ("cm{minus}3", "1/cm3"),
        ("mol L{minus}1", "mol/L"),
    ],
)
def test_pdf_minus_glyphs_preserve_explicit_unit_powers(minus, printed, standard):
    unit = printed.format(minus=minus)
    assert canonical_quantity(70, unit) == canonical_quantity(70, standard)
    assert equivalent_unit_spelling(unit, standard)
    assert convert_reported_value(_reported_value(unit), standard) == pytest.approx(70)


@pytest.mark.parametrize(
    "different", ["A cm‒2", "MA cm‒2", "mA cm+2", "mA cm‒3", "mA/cm‒2"]
)
def test_typographic_minus_does_not_erase_scale_or_dimensions(different):
    assert not equivalent_unit_spelling("mA cm‒2", different)
    assert canonical_quantity(70, "mA cm‒2") != canonical_quantity(70, different)
