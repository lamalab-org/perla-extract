from typing import List, Literal
from typing_extensions import TypedDict


class Ion(TypedDict):
    abbreviation: str  # e.g., 'Cs', 'MA', 'FA', 'PEA'
    common_name: str  # e.g., 'Cesium', 'Methylammonium'
    molecular_formula: str  # e.g., 'Cs+', 'CH5N+'
    coefficient: str  # e.g., '0.75', '1-x'


class UnitValue(TypedDict):
    value: float
    unit: str


class Bandgap(TypedDict):
    value: float  # Range: 0.5-4.0
    unit: str = "eV"


class PerovskiteComposition(TypedDict):
    formula: str
    dimensionality: Literal["0D", "1D", "2D", "3D", "2D/3D"]
    a_ions: List[Ion]
    b_ions: List[Ion]
    x_ions: List[Ion]
    bandgap: Bandgap


class PCE(TypedDict):
    value: float  # Range: 0-40
    unit: str = "%"


class JSC(TypedDict):
    value: float
    unit: Literal["mA cm^-2", "A m^-2", "A cm^-2", "mA m^-2", "uA cm^-2"]


class VOC(TypedDict):
    value: float  # Range: 0-1500
    unit: Literal["V", "mV"]


class FF(TypedDict):
    value: float  # Range: 0-100


class ActiveArea(TypedDict):
    value: float  # Must be > 0
    unit: Literal["cm^2", "mm^2"]


class LightIntensity(TypedDict):
    value: float  # Must be >= 0
    unit: Literal["mW cm^-2", "W m^-2", "mW m^-2", "sun", "lux"]


class Temperature(TypedDict):
    value: float
    unit: Literal["°C", "K"]


class Time(TypedDict):
    value: float
    unit: Literal["s", "min", "h", "days", "weeks", "months", "years"]


class PowerDensity(TypedDict):
    value: float
    unit: Literal["uW/cm^2", "mW/cm^2"]


class LightSource(TypedDict):
    type: Literal[
        "AM 1.5G", "AM 1.5D", "AM 0", "Monochromatic", "White LED", "Other", "Outdoor"
    ]
    description: str
    light_intensity: LightIntensity
    lamp: str


class Pressure(TypedDict):
    value: float
    unit: Literal["Pa", "kPa", "atm", "bar", "mbar", "mmHg", "torr"]


class Humidity(TypedDict):
    value: float  # Range: 0-100
    unit: str = "%"


class Concentration(TypedDict):
    value: float
    unit: Literal["mol/L", "mmol/L", "g/L", "mg/L", "mg/mL", "wt%", "vol%", "M"]


class Solute(TypedDict):
    name: str
    concentration: Concentration


class Volume(TypedDict):
    value: float
    unit: Literal["L", "mL", "μL"]


class Solvent(TypedDict):
    name: str
    volume_fraction: float


class ReactionSolution(TypedDict):
    compounds: List[str]
    solutes: List[Solute]
    volume: Volume
    temperature: Temperature
    solvents: List[Solvent]


class ProcessingStep(TypedDict):
    step_name: str
    method: str
    atmosphere: Literal[
        "Ambient air", "Dry air", "Air", "N2", "Ar", "He", "H2", "Vacuum", "Other"
    ]
    temperature: Temperature
    duration: Time
    antisolvent: str
    solution: ReactionSolution
    additional_parameters: str


class Stability(TypedDict):
    time: Time
    light_intensity: LightIntensity
    humidity: Humidity
    temperature: Temperature
    PCE_T80: Time
    PCE_at_the_start_of_the_experiment: PCE
    PCE_after_1000_hours: PCE
    PCE_at_the_end_of_description: PCE
    potential_bias: Literal[
        "Open circuit",
        "MPPT",
        "Constant potential",
        "Constant current",
        "Constant resistance",
    ]


class Thickness(TypedDict):
    value: float
    unit: Literal["nm", "µm"]


class Layer(TypedDict):
    name: str
    thickness: Thickness
    functionality: Literal[
        "Hole-transport",
        "Electron-transport",
        "Contact",
        "Absorber",
        "Other",
        "Substrate",
    ]
    deposition: List[ProcessingStep]
    additional_treatment: str


class PerovskiteSolarCell(TypedDict):
    perovskite_composition: PerovskiteComposition
    device_architecture: Literal[
        "pin", "nip", "Back contacted", "Front contacted", "Other"
    ]
    pce: PCE
    jsc: JSC
    voc: VOC
    ff: FF
    number_devices: int
    averaged_quantities: bool
    active_area: ActiveArea
    light_source: LightSource
    encapsulated: bool
    additional_notes: str
    # stability: Stability
    layers: List[Layer]


class PerovskiteSolarCells(TypedDict):
    cells: List[PerovskiteSolarCell]


schema_as_str_json_like = """{
        "cells": List[{  # List of PerovskiteSolarCell
            "perovskite_composition": {
                "formula": str,  # IUPAC composition with standard abbreviations
                "dimensionality": Literal["0D", "1D", "2D", "3D", "2D/3D"],
                "a_ions": List[{
                    "abbreviation": str,  # e.g., 'Cs', 'MA', 'FA', 'PEA'
                    "common_name": str,   # e.g., 'Cesium', 'Methylammonium'
                    "molecular_formula": str,  # e.g., 'Cs+', 'CH5N+'
                    "coefficient": str    # e.g., '0.75', '1-x'
                }],
                "b_ions": List[{  # Same structure as a_ions
                    "abbreviation": str,
                    "common_name": str,
                    "molecular_formula": str,
                    "coefficient": str
                }],
                "x_ions": List[{  # Same structure as a_ions
                    "abbreviation": str,
                    "common_name": str,
                    "molecular_formula": str,
                    "coefficient": str
                }],
                "bandgap": {
                    "value": float,  # Range: 0.5-4.0
                    "unit": Literal["eV"]
                }
            },
            "device_architecture": Literal["pin", "nip", "Back contacted", "Front contacted", "Other"],
            "pce": {
                "value": float,  # Range: 0-40
                "unit": Literal["%"]
            },
            "jsc": {
                "value": float,
                "unit": Literal["mA cm^-2", "A m^-2", "A cm^-2", "mA m^-2", "uA cm^-2"]
            },
            "voc": {
                "value": float,  # Range: 0-1500
                "unit": Literal["V", "mV"]
            },
            "ff": {
                "value": float  # Range: 0-100
            },
            "number_devices": int,
            "averaged_quantities": bool,
            "active_area": {
                "value": float,  # Must be > 0
                "unit": Literal["cm^2", "mm^2"]
            },
            "light_source": {
                "type": Literal["AM 1.5G", "AM 1.5D", "AM 0", "Monochromatic", "White LED", "Other", "Outdoor"],
                "description": str,
                "light_intensity": {
                    "value": float,  # Must be >= 0
                    "unit": Literal["mW cm^-2", "W m^-2", "mW m^-2", "sun", "lux"]
                },
                "lamp": str
            },
            "encapsulated": bool,
            "additional_notes": str,
            "stability": {
                "time": {
                    "value": float,
                    "unit": Literal["s", "min", "h", "days", "weeks", "months", "years"]
                },
                "light_intensity": {
                    "value": float,
                    "unit": Literal["mW cm^-2", "W m^-2", "mW m^-2", "sun", "lux"]
                },
                "humidity": {
                    "value": float,  # Range: 0-100
                    "unit": Literal["%"]
                },
                "temperature": {
                    "value": float,
                    "unit": Literal["°C", "K"]
                },
                "PCE_T80": {
                    "value": float,
                    "unit": Literal["s", "min", "h", "days", "weeks", "months", "years"]
                },
                "PCE_at_the_start_of_the_experiment": {
                    "value": float,
                    "unit": Literal["%"]
                },
                "PCE_after_1000_hours": {
                    "value": float,
                    "unit": Literal["%"]
                },
                "PCE_at_the_end_of_description": {
                    "value": float,
                    "unit": Literal["%"]
                },
                "potential_bias": Literal["Open circuit", "MPPT", "Constant potential", "Constant current", "Constant resistance"]
            },
            "layers": List[{
                "name": str,  # Standard abbreviations if possible
                "thickness": {
                    "value": float,
                    "unit": Literal["nm", "µm"]
                },
                "functionality": Literal["Hole-transport", "Electron-transport", "Contact", "Absorber", "Other", "Substrate"],
                "deposition": List[{
                    "step_name": str,
                    "method": str,  # e.g., "Spin-coating", "Evaporation", etc.
                    "atmosphere": Literal["Ambient air", "Dry air", "Air", "N2", "Ar", "He", "H2", "Vacuum", "Other"],
                    "temperature": {
                        "value": float,
                        "unit": Literal["°C", "K"]
                    },
                    "duration": {
                        "value": float,
                        "unit": Literal["s", "min", "h", "days", "weeks", "months", "years"]
                    },
                    "antisolvent": str,
                    "solution": {
                        "compounds": List[str],
                        "solutes": List[{
                            "name": str,
                            "concentration": {
                                "value": float,
                                "unit": Literal["mol/L", "mmol/L", "g/L", "mg/L", "mg/mL", "wt%", "vol%", "M"]
                            }
                        }],
                        "volume": {
                            "value": float,
                            "unit": Literal["L", "mL", "μL"]
                        },
                        "temperature": {
                            "value": float,
                            "unit": Literal["°C", "K"]
                        },
                        "solvents": List[{
                            "name": str,
                            "volume_fraction": float
                        }]
                    },
                    "additional_parameters": Dict[str, any]
                }],
                "additional_treatment": str
            }]
        }]
    }"""
