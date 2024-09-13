from pydantic import BaseModel, Field, confloat, validator, field_validator
from typing import List, Literal, Optional, Union
from quantulum3 import parser
from pint import UnitRegistry
import datetime

ureg = UnitRegistry()

class UnitValue(BaseModel):
    unit: str
    value: confloat(gt=0)

    @validator('unit')
    def check_unit(cls, v, values, field):
        accepted_units = cls.get_accepted_units()
        q = ureg(str(parser.parse(f"1 {v}")[0].unit))
        for accepted_unit in accepted_units:
            if ureg(accepted_unit) == q:
                return accepted_unit
        raise ValueError(f"{field.name} units are incorrect.")

    @classmethod
    def get_accepted_units(cls):
        return []


class PCE(UnitValue):
    unit: Literal['%', ''] = Field(..., description="Unit of PCE, either '%' or empty string for fractions.")

    @validator('value')
    def check_pce_value(cls, v, values):
        unit = values.get('unit', "")
        if unit == '%':
            if not (0 <= v <= 33.8):
                raise ValueError("When unit is '%', value must be between 0 and 33.8")
        elif unit == '':
            if not (0 <= v <= 0.338):
                raise ValueError("When unit is '', value must be between 0 and 0.338")
        return v

class JSC(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['mA cm^-2', 'A m^-2', 'A cm^-2', 'mA m^-2']

    @validator('value')
    def check_jsc_value(cls, v, values):
        unit = values.get('unit', "")
        limits = {
            'mA cm^-2': (7, 40),
            'A m^-2': (70, 400),
            'A cm^-2': (0.007, 0.04),
            'mA m^-2': (70000, 400000)
        }
        if unit in limits:
            low, high = limits[unit]
            if not (low <= v <= high):
                raise ValueError(f"When unit is '{unit}', value must be between {low} and {high}")
        return ureg(f"{v} {unit}").magnitude if unit else v

class VOC(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['V', 'mV', 'kV']

    @field_validator('value', mode="after")
    def check_voc_value(cls, v, info):
        unit = info.data.get('unit')
        limits = {'V': (0, 1.5), 'mV': (0, 1500), 'kV': (0, 0.0015)}
        if unit in limits:
            low, high = limits[unit]
            if not (low <= v <= high):
                raise ValueError(f"When unit is '{unit}', value must be between {low} and {high}")
        return ureg(f"{v} {unit}").to("V").magnitude

class FF(BaseModel):
    value: confloat(ge=0.0, le=1.0) = Field(..., description="Fill factor (a ratio value between 0.0 and 1.0)")

class ActiveArea(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['cm^2', 'mm^2', 'm^2']

    @validator('value')
    def check_active_area_value(cls, v, values):
        unit = values.get('unit', "")
        return ureg(f"{v} {unit}").to("cm^2").magnitude if unit else v

class LightIntensity(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['mW cm^-2', 'W m^-2', 'mW m^-2', 'sun']

    @validator('value')
    def check_light_intensity_value(cls, v, values):
        unit = values.get('unit', "")
        limits = {
            'mW cm^-2': (50, 200),
            'W m^-2': (500, 2000),
            'mW m^-2': (500000, 2000000)
        }
        if unit in limits:
            low, high = limits[unit]
            if not (low <= v <= high):
                raise ValueError(f"When unit is '{unit}', value must be between {low} and {high}")
        if unit == "sun":
            return v
        return ureg(f"{v} {unit}").to("milliwatt / centimeter ** 2").magnitude if unit else v

class Bandgap(BaseModel):
    value: float = Field(..., description="Bandgap energy in electron volts (eV)")
    unit: Literal["eV"] = Field("eV", description="Unit of Bandgap")

class Temperature(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['C', '°C', 'K']

    @validator('value')
    def convert_to_celsius(cls, v, values):
        unit = values.get('unit', '')
        if unit in ['C', '°C']:
            return v
        elif unit == 'K':
            return v - 273.15
        raise ValueError("Invalid temperature unit")

class Time(BaseModel):
    value: confloat(gt=0)
    unit: Literal['s', 'min', 'h']

class Pressure(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['Pa', 'kPa', 'MPa', 'atm', 'bar']

class Concentration(UnitValue):
    @classmethod
    def get_accepted_units(cls):
        return ['mol/L', 'mol/m^3', 'wt%', 'vol%']

class DepositionStep(BaseModel):
    method: str
    atmosphere: str
    temperature: Temperature
    duration: Time
    pressure: Optional[Pressure]
    precursor_solution: Optional[str]
    concentration: Optional[Concentration]
    spin_coating_speed: Optional[int]  # in rpm
    thermal_annealing_temperature: Optional[Temperature]
    thermal_annealing_duration: Optional[Time]
    thermal_annealing_atmosphere: Optional[str]

class PostTreatment(BaseModel):
    method: str
    atmosphere: Optional[str]
    temperature: Optional[Temperature]
    duration: Optional[Time]
    description: Optional[str]


class Device(BaseModel):
    device_stack: List[str]
    perovskite_absorber_chemical_formula: Optional[str]
    scan_direction: Optional[Literal['forward', 'reversed']]
    pce: Optional[PCE]
    jsc: Optional[JSC]
    voc: Optional[VOC]
    ff: Optional[FF]
    active_area: Optional[ActiveArea]
    light_intensity: Optional[LightIntensity]
    bandgap: Optional[Bandgap]
    substrate: Optional[str]
    substrate_additive: Optional[str] = None
    backcontact: Optional[str]
    hole_transport_layer: Optional[str]
    hole_transport_layer_additive: Optional[str] = None
    electron_transport_layer: Optional[str]
    electron_transport_layer_additive: Optional[str] = None

    deposition_steps: List[DepositionStep]
    post_treatments: Optional[List[PostTreatment]]
    encapsulation: Optional[str]
    storage_conditions: Optional[str]

    # New field for additional comments or notes
    additional_notes: Optional[str] = Field(None, description="Any additional comments, observations, or notes about the device or experiment")

class Devices(BaseModel):
    devices: List[Device]
