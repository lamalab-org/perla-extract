from pydantic import BaseModel, Field, validator, confloat, create_model
from typing import List, Literal, Optional, Type, Any, Tuple
from datetime import datetime
from pint import UnitRegistry
from copy import deepcopy
from pydantic.fields import FieldInfo

ureg = UnitRegistry()


def partial_model(model: Type[BaseModel]):
    """Creates a new model with all fields made optional"""

    def make_field_optional(
        field: FieldInfo, default: Any = None
    ) -> Tuple[Any, FieldInfo]:
        new = deepcopy(field)
        new.default = default
        new.required = False
        new.annotation = Optional[field.annotation]
        return new.annotation, new

    return create_model(
        f"Partial{model.__name__}",
        __base__=model,
        __module__=model.__module__,
        **{
            field_name: make_field_optional(field_info)
            for field_name, field_info in model.__fields__.items()
        },
    )


class UnitValue(BaseModel):
    value: Optional[float] = None
    unit: Optional[str] = None


class PCE(UnitValue):
    value: Optional[confloat(ge=0, le=40)] = None
    unit: Optional[Literal["%"]] = None


class JSC(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["mA cm^-2", "A m^-2", "A cm^-2", "mA m^-2", "uA cm^-2"]] = (
        None
    )


class VOC(UnitValue):
    value: Optional[confloat(ge=0, le=1500)] = None
    unit: Optional[Literal["V", "mV"]] = None

    @validator("value", pre=True)
    def check_voc_value(cls, v, values):
        if v is None:
            return v
        unit = values.get("unit")
        if unit == "V" and v > 1.5:
            raise ValueError("When unit is 'V', value must be <= 1.5")
        elif unit == "mV" and v > 1500:
            raise ValueError("When unit is 'mV', value must be <= 1500")
        return v


class FF(BaseModel):
    value: Optional[confloat(ge=0.0, le=100)] = None


class ActiveArea(UnitValue):
    value: Optional[confloat(gt=0)] = None
    unit: Optional[Literal["cm^2", "mm^2"]] = None

    @validator("value", pre=True)
    def convert_to_cm2(cls, v, values):
        if v is None:
            return v
        unit = values.get("unit")
        if unit == "mm^2":
            return v / 100
        return v


class LightIntensity(UnitValue):
    value: Optional[confloat(ge=0)] = None
    unit: Optional[Literal["mW cm^-2", "W m^-2", "mW m^-2", "sun", "lux"]] = None


class Bandgap(UnitValue):
    value: Optional[confloat(ge=0.5, le=4.0)] = None
    unit: Optional[Literal["eV"]] = None


class Temperature(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["°C", "K"]] = None

    @validator("value", pre=True)
    def convert_to_celsius(cls, v, values):
        if v is None:
            return v
        if values.get("unit") == "K":
            return v - 273.15
        return v


class Time(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["s", "min", "h"]] = None


class PowerDensity(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["uW/cm^2", "mW/cm^2"]] = None


class LightSource(BaseModel):
    type: Optional[
        Literal[
            "AM 1.5G",
            "AM 1.5D",
            "AM 0",
            "Monochromatic",
            "White LED",
            "Other",
            "Outdoor",
        ]
    ] = None
    description: Optional[str] = None
    light_intensity: Optional[LightIntensity] = None
    lamp: Optional[str] = None


class Pressure(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["Pa", "kPa", "atm", "bar", "mbar", "mmHg", "Torr"]] = None


class Humidity(UnitValue):
    value: Optional[confloat(ge=0, le=100)] = None
    unit: Optional[Literal["%"]] = None


class Concentration(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["mol/L", "mmol/L", "g/L", "mg/L", "wt%", "vol%", "M"]] = None


class Volume(UnitValue):
    value: Optional[float] = None
    unit: Optional[Literal["L", "mL", "μL"]] = None


class ProcessingAtmosphere(BaseModel):
    type: Optional[str] = None
    pressure: Optional[Pressure] = None
    relative_humidity: Optional[Humidity] = None


class Solvent(BaseModel):
    name: Optional[str] = None
    volume: Optional[float] = None


class ReactionSolution(BaseModel):
    compounds: Optional[List[str]] = None
    concentrations: Optional[List[Concentration]] = None
    volume: Optional[Volume] = None
    temperature: Optional[Temperature] = None
    solvent: Optional[Solvent] = None


class ProcessingStep(BaseModel):
    step_name: Optional[str] = None
    method: Optional[str] = None
    time: Optional[Time] = None
    atmosphere: Optional[ProcessingAtmosphere] = None
    temperature: Optional[Temperature] = None
    duration: Optional[Time] = None
    antisolvent: Optional[Solvent] = None
    gas: Optional[str] = None
    solution: Optional[ReactionSolution] = None
    additional_parameters: Optional[dict] = None
    number_devices: Optional[int] = None
    averaged_quantities: Optional[bool] = None


class Deposition(BaseModel):
    steps: Optional[List[ProcessingStep]] = None
    additional_notes: Optional[str] = None


class Stability(BaseModel):
    time: Optional[Time] = None
    light_intensity: Optional[LightIntensity] = None
    humidity: Optional[Humidity] = None
    temperature: Optional[Temperature] = None
    PCE_T80: Optional[Time] = None
    PCE_at_the_start_of_the_experiment: Optional[PCE] = None
    PCE_after_1000_hours: Optional[PCE] = None
    PCE_at_the_end_of_description: Optional[PCE] = None


class Layer(BaseModel):
    name: Optional[str] = None
    thickness: Optional[float] = None
    functionality: Optional[
        Literal[
            "backcontact",
            "hole-transport",
            "electron-transport",
            "contact",
            "absorber",
            "other",
            "substrate",
        ]
    ] = None
    deposition: Optional[Deposition] = None


class PerovskiteSolarCell(BaseModel):
    cell_stack: Optional[List[str]] = None
    perovskite_composition: Optional[str] = None
    device_architecture: Optional[
        Literal["pin", "nip", "back-contacted", "front-contacted"]
    ] = None
    pce: Optional[PCE] = None
    jsc: Optional[JSC] = None
    voc: Optional[VOC] = None
    ff: Optional[FF] = None
    active_area: Optional[ActiveArea] = None
    light_source: Optional[LightSource] = None
    bandgap: Optional[Bandgap] = None
    encapsulation: Optional[str] = None
    additional_notes: Optional[str] = None
    stability: Optional[Stability] = None
    layers: Optional[List[Layer]] = None

    @validator("cell_stack")
    def check_cell_stack(cls, v):
        if v is not None and len(v) < 4:
            raise ValueError("Cell stack must have at least 4 layers")
        return v


class PerovskiteSolarCells(BaseModel):
    cells: Optional[List[PerovskiteSolarCell]] = None
