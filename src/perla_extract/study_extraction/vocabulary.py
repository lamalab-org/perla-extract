"""Small shared vocabularies used by interpretation and downstream adapters."""

from typing import Literal, get_args

NormalizedAtmosphere = Literal[
    "Ambient air",
    "Dry air",
    "Air",
    "N2",
    "Ar",
    "He",
    "H2",
    "Vacuum",
    "Other",
    "Unknown",
]
NORMALIZED_ATMOSPHERES = frozenset(get_args(NormalizedAtmosphere))
