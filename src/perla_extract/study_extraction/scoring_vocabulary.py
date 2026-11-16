"""Small, explicit equivalences for scientific names, not fuzzy text similarity.

Unknown names remain literal. Extending these tables changes scoring policy and
requires positive and negative examples. Formula symbols retain case and charge;
this module does not infer mixtures, morphology, doping or unspecified chemistry.
"""

from __future__ import annotations

import re

from .scoring_claims import scientific_text, scientific_value_text

METRIC_ALIASES = {
    "powerconversionefficiency": "pce",
    "opencircuitvoltage": "voc",
    "shortcircuitcurrentdensity": "jsc",
    "fillfactor": "ff",
    "referenceefficiencyretained": "efficiencyretention",
    "pceretention": "efficiencyretention",
    "referencecell'sefficiencyretained": "efficiencyretention",
    "errorbarstatistic": "errorbarstatistic",
    "errorbarstatisticforphotovoltaicparameters": "errorbarstatistic",
    "samplesize": "sample_size",
    "chlorineinresultingperovskitefilms": "chlorineinresultingperovskitefilm",
}


def property_name(name: str) -> str:
    """Normalize typography and checked property synonyms; retain unknown concepts."""
    pair = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", scientific_text(name))
    if pair:
        parts = [re.sub(r"[\s_-]+", "", p.casefold()) for p in pair.groups()]
        concepts = [METRIC_ALIASES.get(p, p) for p in parts]
        if concepts[0] == concepts[1] and concepts[0] in {"pce", "voc", "jsc", "ff"}:
            return concepts[0]
    key = re.sub(r"[\s_-]+", "", scientific_text(name).casefold())
    return METRIC_ALIASES.get(key, key)


def error_bar_statistic(value: str) -> str:
    """Recognize named uncertainty statistics without guessing from 'error bars'."""
    key = scientific_text(value).casefold().replace("_", " ")
    return {
        "standard deviation": "standard_deviation",
        "standard error": "standard_error",
        "standard error of the mean": "standard_error",
        "confidence interval": "confidence_interval",
    }.get(key, value)


TREATMENT_NAMES = {
    "atmosphere": "atmosphere",
    "treatmentatmosphere": "atmosphere",
    "temperature": "temperature",
    "annealingtemperature": "temperature",
    "duration": "duration",
    "annealingtime": "duration",
    "annealingduration": "duration",
    "treatmentduration": "duration",
    "treatment": "treatment",
    "concentration": "concentration",
    "pressure": "pressure",
    "relativehumidity": "relativehumidity",
    "illumination": "illumination",
    "illuminationintensity": "illuminationintensity",
    "electrodepositionpotential": "electrodeposition_potential",
    "depositionpotential": "electrodeposition_potential",
    "polymerizationchargedensity": "charge_density",
    "chargedensity": "charge_density",
    "postreductionpotential": "postreduction_potential",
    "postreductiontime": "postreduction_duration",
    "postreductionduration": "postreduction_duration",
}


def treatment_condition_name(name: str) -> str | None:
    """Recognize stated treatment inputs, preserving explicit material qualifiers.

    A known material may prefix a registered condition, e.g. ``PEDOT deposition
    potential``. Unknown qualifiers, starting/ending temperatures and outcomes
    stay literal and cannot establish a treatment identity.
    """
    direct = TREATMENT_NAMES.get(property_name(name))
    if direct is not None:
        return direct
    text = scientific_text(name)
    for separator in re.finditer(r"\s+", text):
        material, condition = text[: separator.start()], text[separator.end() :]
        canonical = TREATMENT_NAMES.get(property_name(condition))
        if canonical is not None and known_material(material):
            return f"{canonical}@{material_name(material)}"
    return None


MATERIAL_ROLES = {
    "perovskiteabsorber": "absorber",
    "hostabsorber": "absorber",
    "absorber": "absorber",
    "holetransportmaterial": "hole_transport_layer",
    "holetransportlayer": "hole_transport_layer",
    "htl": "hole_transport_layer",
    "electrontransportmaterial": "electron_transport_layer",
    "electrontransportlayer": "electron_transport_layer",
    "etl": "electron_transport_layer",
    "transparentelectrode": "transparent_electrode",
    "backelectrode": "back_electrode",
    "electrode": "electrode",
    "contact": "contact",
    "substrate": "substrate",
    "precursor": "precursor",
    "perovskiteprecursor": "precursor",
    "precursoradditive": "precursor_additive",
}
FUNCTION_ROLES = frozenset(MATERIAL_ROLES.values()) - {
    "precursor",
    "precursor_additive",
}


def material_role(role: str | None) -> str | None:
    """Normalize a constituent's stated function, retaining unknown qualifiers."""
    return (
        MATERIAL_ROLES.get(property_name(role), scientific_text(role)) if role else None
    )


def material_stage(role: str | None) -> str:
    """An explicit precursor role is recipe scope; unknown roles remain unspecified."""
    canonical = material_role(role)
    if canonical in {"precursor", "precursor_additive"}:
        return "recipe"
    return "finished" if canonical in FUNCTION_ROLES else "unspecified"


def whole_material_role(role: str | None) -> bool:
    """An additive or crystallographic-site constituent cannot stand for a whole layer."""
    return role is None or material_role(role) in FUNCTION_ROLES


# Full chemical names are case-insensitive; formula symbols are not.
MATERIAL_NAMES = {
    "gold": "Au",
    "silver": "Ag",
    "aluminium": "Al",
    "aluminum": "Al",
    "copper": "Cu",
    "nickel": "Ni",
    "titanium dioxide": "TiO2",
    "tin dioxide": "SnO2",
    "zinc oxide": "ZnO",
    "indium tin oxide": "ITO",
    "fluorine-doped tin oxide": "FTO",
    "poly(3-hexylthiophene)": "P3HT",
    "poly-3-hexylthiophene": "P3HT",
    "methylammonium lead triiodide": "MAPbI3",
    "poly(3,4-ethylenedioxythiophene)": "PEDOT",
    "glass": "glass",
    "spiro-meotad": "spiro-MeOTAD",
    "spiromeotad": "spiro-MeOTAD",
    "pc61bm": "PC61BM",
    "fluorinedoped tin oxide conducting glass": "FTO",
}
_SYMBOLS = set(MATERIAL_NAMES.values())
_CONTAINERS = (" layer", " electrode", " contact", " substrate")
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–"})


def _known_material(text: str) -> str | None:
    if text in _SYMBOLS:
        return text
    return MATERIAL_NAMES.get(text.casefold())


def material_name(value: str) -> str:
    """Normalize checked synonyms and redundant containers in material fields.

    ``Gold (Au)`` is accepted only when both parts independently denote the same
    material. ``Gold (Ag)``, ``doped TiO2`` and ``TiO2 nanoparticles`` stay literal.
    Container words add no material identity; role and form remain separate facts.
    """

    text = scientific_value_text(value).translate(_DASHES)
    known = _known_material(text)
    if known is not None:
        return known
    for suffix in _CONTAINERS:
        if text.casefold().endswith(suffix):
            base = text[: -len(suffix)]
            known = _known_material(scientific_value_text(base))
            if known is not None:
                return known
    pair = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", text)
    if pair:
        left, right = (
            _known_material(scientific_value_text(p.strip())) for p in pair.groups()
        )
        if left is not None and left == right:
            return left
    # A manufacturer is descriptive only when the full name and abbreviation
    # independently establish the same material. Unknown/doped names stay literal.
    vendor = re.fullmatch(r"(.+?)\s*\(([^(),]+),\s*[^()]+\)", text)
    if vendor:
        left, right = (_known_material(p.strip()) for p in vendor.groups())
        if left is not None and left == right:
            return left
    form = re.fullmatch(r"(compact|mesoporous)\s+(.+?)(?: layer)?", text, re.IGNORECASE)
    if form and known_material(form[2]):
        return f"{form[1].lower()} {material_name(form[2])}"
    if text.casefold().endswith(" htm"):
        known = _known_material(text[:-4])
        if known is not None:
            return known
    return text


def known_material(value: str) -> bool:
    """Limit automatic identity-conflict flags to the checked material vocabulary."""
    return material_name(value) in _SYMBOLS


def scoped_property_name(name: str, material: str) -> str:
    """Remove a redundant material qualifier from a known layer property.

    Only the actual material's checked name/symbol can qualify the property. Thus
    gold-contact thickness and thickness on a gold layer agree, but silver-contact
    thickness on that layer does not. Arbitrary property paraphrases stay literal.
    """

    source_key = re.sub(r"[\s_-]+", "", scientific_text(name).translate(_DASHES))
    key = source_key.casefold()
    subject = material_name(material)
    if subject not in _SYMBOLS:
        return key
    aliases = [
        subject,
        *(n for n, symbol in MATERIAL_NAMES.items() if symbol == subject),
    ]
    properties = {
        "thickness",
        "sheetresistance",
        "bandgap",
        "roughness",
        "workfunction",
    }
    containers = ("", "layer", "film", "contact", "electrode", "substrate")
    for alias in aliases:
        # Chemical symbols retain case even inside a prose property name.
        candidate = source_key if alias == subject else key
        prefix = re.sub(r"[\s_-]+", "", alias if alias == subject else alias.casefold())
        if not candidate.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        for container in containers:
            if rest.startswith(container) and rest[len(container) :] in properties:
                return rest[len(container) :]
    return key


def scientific_concept(
    group: str, name: str, stage: str | None, *, outcome: bool = False
) -> tuple[str, str]:
    """Namespace assertions with different meanings even when their names coincide.

    A population outcome and an individual measurement can recover the same
    information. A recipe ingredient and a finished material cannot. Aging
    conditions/outcomes retain their test scope rather than becoming static device
    properties. Context determines attribution after this candidate gate.
    """
    if group == "processing":
        # Storage under subject context or recipe conditions does not create a
        # different quantity. Attribution still checks owner and operation.
        return group, name.removeprefix("treatment:")
    if group in {"performance", "population"}:
        return "measurement", name
    if group == "stability":
        return "aging_outcome" if outcome else "aging_protocol", name
    if group in {"composition", "stack"}:
        return (
            "recipe"
            if stage == "recipe" or name.startswith("precursor")
            else "material"
        ), name
    return group, name
