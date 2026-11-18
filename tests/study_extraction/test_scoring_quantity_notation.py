"""Equivalent quantities retain credit without erasing scientific qualifiers."""

import pytest

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.models import ReportedValue
from perla_extract.study_extraction.scoring_facts import equal_value
from tests.study_extraction.test_evaluation import EVIDENCE, study


def quantity(raw, number=None, unit=None):
    return ReportedValue(
        name="property",
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


@pytest.mark.parametrize(
    "left,right",
    [
        (quantity("E = 1.0 V", 1, "V"), quantity("1 V", 1, "V")),
        (
            quantity("Q pol = 10 mC cm -2", 10, "mC cm -2"),
            quantity("10 mC cm-2", 10, "mC cm-2"),
        ),
        (quantity("15 Ω sq -1", 15, "Ω sq -1"), quantity("15 Ω sq−1", 15, "Ω sq−1")),
        (quantity("15 Ω/□"), quantity("0.015 kΩ/square", 0.015, "kΩ/square")),
        (quantity("15 ohms per square"), quantity("15 Ω/sq", 15, "Ω/sq")),
        (quantity("2 × 10 -6 mbar", 2e-6, "mbar"), quantity("0.0002 Pa", 0.0002, "Pa")),
        (quantity("2 × 10⁻⁶ mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity("2 × 10^{-6} mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity(r"2 \times 10^-6 mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity("20 ± 1 °C"), quantity("293.15 ± 1 K", 293.15, "K")),
        (quantity("68 ± 1.8 degF", 68), quantity("20 ± 1 °C", 20, "°C")),
        (quantity("1–2 h"), quantity("3600–7200 s", None, "s")),
        (quantity("2 × 10⁻³–3 × 10⁻³ m"), quantity("2–3 mm", None, "mm")),
    ],
)
def test_complete_notation_changes_preserve_value_and_public_fact_credit(left, right):
    originals = left.model_dump(), right.model_dump()
    assert equal_value(left, right, 1e-6, 0)
    assert equal_value(right, left, 1e-6, 0)
    for expected, actual in ((left, right), (right, left)):
        result = evaluate_study(study(pce=expected), study(prefix="pred", pce=actual))
        assert result.core_facts.groups["performance"].matched == 1
        assert result.core_facts.groups["performance"].f1 == 1
    assert (left.model_dump(), right.model_dump()) == originals


@pytest.mark.parametrize(
    "left,right",
    [
        (quantity("mean = 1.0 V", 1, "V"), quantity("1 V", 1, "V")),
        (quantity("E < 1.0 V", 1, "V"), quantity("1 V", 1, "V")),
        (quantity("E = 1.0 V", 2, "V"), quantity("1 V", 1, "V")),
        (quantity("15 Ω/sq", 15, "Ω/sq"), quantity("15 Ω/m²", 15, "Ω/m²")),
        (quantity("15 Ω/sq", 15, "Ω/sq"), quantity("15 kΩ/sq", 15, "kΩ/sq")),
        (quantity("15 Ω/sq", 15, "kΩ/sq"), quantity("15 Ω/sq", 15, "Ω/sq")),
        (quantity("2 × 10⁻⁶ mbar", 3e-6, "mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity("2 × 10⁻⁶ mbar", 2e-6, "Pa"), quantity("2e-6 Pa", 2e-6, "Pa")),
        (quantity("2 × 10⁻⁶ mbar"), quantity("2 × 10⁻⁵ mbar")),
        (quantity("<2 × 10⁻⁶ mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity("approximately 2 × 10⁻⁶ mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity("2 × 10⁹⁹⁹ mbar"), quantity("0 mbar", 0, "mbar")),
        (quantity("2 × 10⁻⁹⁹⁹ mbar"), quantity("0 mbar", 0, "mbar")),
        (quantity("2e-999 mbar", 0, "mbar"), quantity("0 mbar", 0, "mbar")),
        (quantity("20 ± 1e-999 °C"), quantity("20 ± 0 °C")),
        (quantity("2 × 10-6 + 1 mbar"), quantity("3e-6 mbar", 3e-6, "mbar")),
        (quantity("2 × 10^{-6 mbar"), quantity("2e-6 mbar", 2e-6, "mbar")),
        (quantity("20 ± 1 °C", 21), quantity("293.15 ± 1 K", 293.15, "K")),
        (quantity("20 ± 1 °C", 20, "K"), quantity("20 ± 1 K", 20, "K")),
        (quantity("20 ± 1 °C SD"), quantity("293.15 ± 1 K", 293.15, "K")),
        (quantity("20 ± 1 °C"), quantity("293.15 ± 2 K", 293.15, "K")),
        (quantity("20 ± 1"), quantity("20 ± 1 dimensionless", 20, "dimensionless")),
        (quantity("1–2 h", 1), quantity("3600–7200 s", None, "s")),
        (quantity("2–1 h"), quantity("3600–7200 s", None, "s")),
        (quantity("1–2 h"), quantity("1.5 ± 0.5 h", 1.5, "h")),
        (quantity("1–2 h"), quantity("60–121 min", None, "min")),
        (quantity("1–2 invented_units"), quantity("1–2 other_units")),
    ],
)
def test_notation_normalization_preserves_contradictions_bounds_and_units(left, right):
    assert not equal_value(left, right, 1e-6, 0)
    assert not equal_value(right, left, 1e-6, 0)
