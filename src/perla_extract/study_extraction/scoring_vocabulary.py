"""Small, explicit equivalences for scientific names, not fuzzy text similarity.

Unknown names remain literal. Extending these tables changes scoring policy and
requires positive and negative examples. Formula symbols retain case and charge;
this module does not infer mixtures, morphology, doping or unspecified chemistry.
"""

from __future__ import annotations

import re

from .scoring_claims import scientific_text, scientific_value_text

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
    return text


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
