"""Measure explicit comparison capabilities using independently specified cases.

These are synthetic mathematical/notation examples authored for this audit, not
expert calibration labels or an estimate of error frequency in published papers.
Expected equivalence is not changed to accommodate a conservative comparator.
"""

from perla_extract.study_extraction.models import ReportedValue
from perla_extract.study_extraction.scoring_facts import equal_value

# Triples are raw text, parsed number and explicit unit. Ranges and uncertainty
# retain their full claims; matching only a central number would be incorrect.
CASES = [
    ("percent_fraction", ("20", 20, "%"), ("0.20", 0.2, "dimensionless"), True),
    ("hours_seconds", ("1", 1, "h"), ("3600", 3600, "s"), True),
    ("temperature_offset", ("65", 65, "°C"), ("338.15", 338.15, "K"), True),
    ("nanometres_metres", ("1", 1, "nm"), ("1e-9", 1e-9, "m"), True),
    ("changed_value", ("20", 20, "%"), ("21", 21, "%"), False),
    ("wrong_unit_scale", ("23", 23, "mA/cm2"), ("23", 23, "A/cm2"), False),
    ("missing_unit", ("20", 20, "%"), ("20", 20, None), False),
    ("bound_not_point", (">20", 20, "%"), ("20", 20, "%"), False),
    ("uncertainty_not_point", ("20 ± 1", 20, "%"), ("20", 20, "%"), False),
    ("range_conversion", ("1–2", None, "h"), ("60–120", None, "min"), True),
    (
        "uncertainty_conversion",
        ("20 ± 1", 20, "%"),
        ("0.20 ± 0.01", 0.2, "dimensionless"),
        True,
    ),
    ("unicode_formula", ("CH₃NH₃PbI₃", None, None), ("CH3NH3PbI3", None, None), True),
    (
        "latex_formula",
        ("CH_{3}NH_{3}PbI_{3}", None, None),
        ("CH3NH3PbI3", None, None),
        True,
    ),
    ("chemical_case", ("CoO", None, None), ("COO", None, None), False),
]


def probe():
    """Retain every discrepancy, including documented unsupported equivalences."""
    results = []
    for name, left, right, expected in CASES:
        values = [
            ReportedValue(
                name="synthetic property",
                raw_value=raw,
                value_number=number,
                unit=unit,
                evidence=[
                    {
                        "block_id": "synthetic",
                        "quote": "Synthetic arithmetic/notation probe, not paper evidence.",
                    }
                ],
            )
            for raw, number, unit in (left, right)
        ]
        actual = equal_value(*values, relative=1e-6, absolute=0)
        results.append(
            {
                "case": name,
                "left": left,
                "right": right,
                "expected_equivalent": expected,
                "scorer_equivalent": actual,
                "agrees": actual == expected,
            }
        )
    return results
