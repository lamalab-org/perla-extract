"""Required physical meanings; expectations do not use the production unit parser.

Every parametrized case runs, including inputs the parser cannot interpret. These
tests establish quantity behavior, not source grounding or extraction accuracy.
"""

import pytest

from perla_extract.study_extraction.models import EvidenceCitation, ReportedValue
from perla_extract.study_extraction.quantities import (
    CompoundClaim,
    LiteralClaim,
    ScalarClaim,
    convert_reported_value,
    equal_claims,
    interpret_quantity,
    normalize_claim,
    project_scalar,
    scientific_value_text,
)
from perla_extract.study_extraction.quantity_specs import QUANTITY_SPECS


def reported(name, raw, number=None, unit=None):
    return ReportedValue(
        name=name,
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=[EvidenceCitation(block_id="source", quote=raw)],
    )


@pytest.mark.parametrize(
    "kind,value,expected,base_unit",
    [
        ("pce", reported("PCE", "20%", 20, "%"), 0.20, "dimensionless"),
        ("ff", reported("FF", "80%", 80, "%"), 0.80, "dimensionless"),
        (
            "voc",
            reported("Voc", "1000 mV", 1000, "mV"),
            1,
            "kilogram * meter ** 2 / ampere / second ** 3",
        ),
        ("jsc", reported("Jsc", "20 mA/cm²", 20, "mA/cm²"), 200, "ampere / meter ** 2"),
        ("temperature", reported("temperature", "20 °C", 20, "°C"), 293.15, "kelvin"),
        ("duration", reported("duration", "2 h", 2, "h"), 7200, "second"),
        ("thickness", reported("thickness", "100 nm", 100, "nm"), 1e-7, "meter"),
        ("area", reported("active area", "1 cm²", 1, "cm²"), 1e-4, "meter ** 2"),
        (
            "spin_speed",
            reported("spin speed", "60 rpm", 60, "rpm"),
            6.283185307179586,
            "radian / second",
        ),
        (
            "relative_humidity",
            reported("relative humidity", "50% RH", 50, "%"),
            0.5,
            "dimensionless",
        ),
    ],
)
def test_every_registered_quantity_has_an_independently_expected_physical_meaning(
    kind, value, expected, base_unit
):
    original = value.model_dump_json()
    result = interpret_quantity(value)
    assert result.kind == kind
    assert result.status == "normalized"
    assert isinstance(result.claim, ScalarClaim)
    assert result.claim.canonical is not None
    assert result.claim.canonical.magnitude == pytest.approx(expected)
    assert result.claim.canonical.unit == base_unit
    assert value.model_dump_json() == original


def test_required_physical_cases_cover_registry_without_parser_based_skips():
    required = {
        "pce",
        "ff",
        "voc",
        "jsc",
        "temperature",
        "duration",
        "thickness",
        "area",
        "spin_speed",
        "relative_humidity",
    }
    assert set(QUANTITY_SPECS) == required


@pytest.mark.parametrize(
    "name,unit",
    [
        ("PCE", "s"),
        ("FF", "s"),
        ("Voc", "s"),
        ("Jsc", "s"),
        ("temperature", "s"),
        ("duration", "V"),
        ("thickness", "s"),
        ("active area", "s"),
        ("spin speed", "m"),
        ("relative humidity", "s"),
    ],
)
def test_each_known_quantity_rejects_a_different_physical_dimension(name, unit):
    result = interpret_quantity(reported(name, f"1 {unit}", 1, unit))
    assert result.status == "contradiction"
    assert result.reason == "incompatible_dimension"


@pytest.mark.parametrize("unit", ["%", "% RH", "%RH", "percent RH"])
def test_humidity_notation_is_interpreted_only_with_its_known_quantity(unit):
    value = reported("relative humidity", "50% RH", 50, unit)
    result = interpret_quantity(value)
    assert result.status == "normalized"
    assert result.claim.canonical.magnitude == pytest.approx(0.5)
    assert result.claim.canonical.unit == "dimensionless"
    other = interpret_quantity(value.model_copy(update={"name": "inductance"}))
    assert other.kind is None
    assert other.status != "normalized" or other.claim.canonical.unit != "dimensionless"


@pytest.mark.parametrize(
    "raw,number,unit,percent,source",
    [
        ("0.2", 0.2, None, 0.2, "percentage_points"),
        ("0.2", 0.2, "dimensionless", 20, "reported"),
        ("0.2 dimensionless", None, None, 20, "raw_value"),
        ("20%", None, None, 20, "raw_value"),
    ],
)
def test_explicit_fraction_and_missing_percentage_unit_have_different_conventions(
    raw, number, unit, percent, source
):
    value = reported("PCE", raw, number, unit)
    result = interpret_quantity(value)
    assert result.unit_source == source
    assert project_scalar(value, "percent") == pytest.approx(percent)


@pytest.mark.parametrize(
    "name,raw,number,unit,reason",
    [
        ("PCE", "20%", 2, "%", "raw_parsed_conflict"),
        ("duration", "20 ms", 0.02, "V", "raw_parsed_conflict"),
        ("temperature", "20 °C", 20, "K", "raw_parsed_conflict"),
        ("Voc", "1 A", 1, "A", "incompatible_dimension"),
        ("PCE", "18–20%", 19, "%", "range_has_scalar"),
    ],
)
def test_explicit_contradictions_remain_visible_and_never_export_or_match_themselves(
    name, raw, number, unit, reason
):
    value = reported(name, raw, number, unit)
    original = value.model_dump_json()
    result = interpret_quantity(value)
    assert result.status == "contradiction"
    assert result.reason == reason
    assert isinstance(result.claim, LiteralClaim)
    assert not equal_claims(result.claim, result.claim, 0.01, 0)
    assert project_scalar(value, "%", allow_qualified=True) is None
    assert value.model_dump_json() == original


def test_consistency_compares_complete_explicit_physical_quantities():
    converted = reported("duration", "20 ms", 0.02, "s")
    plain = reported("duration", "20 ms", 20, "ms")
    assert interpret_quantity(converted).status == "normalized"
    assert interpret_quantity(converted).unit_source == "raw_value"
    assert equal_claims(normalize_claim(converted), normalize_claim(plain), 0, 0)
    assert project_scalar(converted, "s") == pytest.approx(0.02)


@pytest.mark.parametrize("raw", ["20 ms", "1–2 ms", "20 ± 1 ms"])
def test_a_redundant_unit_with_different_scale_and_no_parsed_number_is_unresolved(raw):
    value = reported("duration", raw, None, "s")
    assert interpret_quantity(value).status == "literal"
    assert interpret_quantity(value).reason == "ambiguous_unit_scale"
    assert project_scalar(value, "s", allow_qualified=True) is None
    incompatible = value.model_copy(update={"unit": "V"})
    assert interpret_quantity(incompatible).status == "contradiction"


@pytest.mark.parametrize(
    "raw", ["20 unknownunit", "0.4:0.9:0.95:0.05", "3:1", "15 s before the end"]
)
def test_uninterpreted_numeric_prefix_without_parsed_metadata_is_not_a_contradiction(
    raw,
):
    value = reported("PCE", raw)
    result = interpret_quantity(value)
    assert result.status == "literal"
    assert result.reason == "unsupported_unit"
    assert equal_claims(result.claim, result.claim, 0.01, 0)
    assert project_scalar(value, "%", allow_qualified=True) is None


@pytest.mark.parametrize(
    "name,raw,number,target,expected",
    [
        ("PCE (%)", "20", 20, "%", 20),
        ("Voc [mV]", "1000", 1000, "V", 1),
        ("PCE [dimensionless]", "0.2", 0.2, "%", 20),
        ("relative humidity [% RH]", "50", 50, "%", 50),
    ],
)
def test_complete_field_name_units_precede_a_missing_unit_convention(
    name, raw, number, target, expected
):
    value = reported(name, raw, number)
    result = interpret_quantity(value)
    assert result.status == "normalized"
    assert result.unit_source == "field_name"
    assert project_scalar(value, target) == pytest.approx(expected)


def test_absolute_temperature_and_its_uncertainty_have_different_offset_rules():
    value = reported("temperature", "20 ± 1 °C", 20, "°C")
    result = interpret_quantity(value)
    assert result.status == "normalized"
    assert isinstance(result.claim, CompoundClaim)
    assert result.claim.first == pytest.approx(293.15)
    assert result.claim.second == pytest.approx(1)
    assert result.claim.unit == "kelvin"
    assert project_scalar(value, "K") is None
    assert project_scalar(value, "K", allow_qualified=True) == pytest.approx(293.15)


def test_bare_temperature_C_does_not_reinterpret_charge_C():
    temperature = interpret_quantity(reported("temperature", "100 C", 100, "C"))
    charge = interpret_quantity(reported("charge", "100 C", 100, "C"))
    assert temperature.status == charge.status == "normalized"
    assert temperature.claim.canonical.magnitude == pytest.approx(373.15)
    assert temperature.claim.canonical.unit == "kelvin"
    assert charge.claim.canonical.magnitude == 100
    assert charge.claim.canonical.unit == "ampere * second"


def test_complete_range_keeps_endpoints_and_cannot_supply_an_exact_scalar():
    value = reported("duration", "1–2 h", None, "h")
    result = interpret_quantity(value)
    assert isinstance(result.claim, CompoundClaim)
    assert result.claim.kind == "range"
    assert (result.claim.first, result.claim.second, result.claim.unit) == (
        3600,
        7200,
        "second",
    )
    assert project_scalar(value, "s", allow_qualified=True) is None
    assert not equal_claims(
        result.claim, normalize_claim(reported("duration", "1.5 h", 1.5, "h")), 0.01, 0
    )


@pytest.mark.parametrize("raw", ["∼14 %", "approximately 14%", "about 14%"])
def test_approximation_requires_explicit_qualified_export_and_is_not_an_exact_claim(
    raw,
):
    value = reported("PCE", raw, 14, "%")
    result = interpret_quantity(value)
    assert result.status == "normalized"
    assert result.claim.qualifier == "approximate"
    assert project_scalar(value, "%") is None
    assert convert_reported_value(value, "%", allow_qualified=True) == 14
    assert not equal_claims(
        result.claim, normalize_claim(reported("PCE", "14%", 14, "%")), 0.01, 0
    )


@pytest.mark.parametrize(
    "raw", ["<20%", "mean 20%", "20% certified", "20 invented_unit"]
)
def test_arbitrary_qualifiers_and_unsupported_units_never_acquire_a_scalar_center(raw):
    value = reported("PCE", raw, 20, None)
    assert interpret_quantity(value).status == "literal"
    assert project_scalar(value, "%", allow_qualified=True) is None


@pytest.mark.parametrize("raw", ["20% certified", "20 ± 1% SD", "20 unknown_unit"])
def test_uninterpreted_suffix_is_not_mislabeled_as_a_proven_physical_contradiction(raw):
    result = interpret_quantity(reported("PCE", raw, 20, "%"))
    assert result.status == "literal"
    assert result.reason == "unsupported_unit"


def test_cache_includes_quantity_kind_and_does_not_mutate_raw_inputs():
    value = reported("relative humidity", "50% RH", 50, "% RH")
    for name in ("relative humidity", "inductance", "relative humidity"):
        candidate = value.model_copy(update={"name": name})
        result = interpret_quantity(candidate)
        assert (result.kind == "relative_humidity") == (name == "relative humidity")
    assert value.unit == "% RH"
    assert value.raw_value == "50% RH"


def test_scientific_typography_preserves_case_charge_and_subscript_distinctions():
    assert scientific_value_text("Co") != scientific_value_text("CO")
    assert scientific_value_text("Fe³⁺") != scientific_value_text("Fe₃⁺")


@pytest.mark.parametrize(
    "raw,unit", [("20 mA cm 2", "mA/cm2"), ("20 mM TiCl4", "mM"), ("20 u C", "°C")]
)
def test_implicit_product_or_qualifier_is_unresolved_when_unit_metadata_disagrees(
    raw, unit
):
    value = reported("property", raw, 20, unit)
    result = interpret_quantity(value)
    assert result.status == "literal"
    assert result.reason == "ambiguous_unit_notation"
    assert project_scalar(value, unit) is None


def test_explicit_unit_product_and_actual_numeric_disagreements_remain_contradictions():
    explicit_dimension = reported("property", "20 mA cm²", 20, "mA/cm2")
    same_units = reported("property", "20 kg m", 2, "kg m")
    assert interpret_quantity(explicit_dimension).status == "contradiction"
    assert interpret_quantity(same_units).status == "contradiction"


@pytest.mark.parametrize("micro", ["µ", "μ"])
def test_split_micro_prefix_is_typography_not_a_product(micro):
    value = reported("volume", f"50 {micro} L", 50, f"{micro}L")
    result = interpret_quantity(value)
    assert result.status == "normalized"
    assert result.claim.canonical.magnitude == pytest.approx(5e-8)
    assert result.claim.canonical.unit == "meter ** 3"
