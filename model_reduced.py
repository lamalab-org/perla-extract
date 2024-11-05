from pydantic import BaseModel, Field, validator, confloat
from typing import List, Literal, Optional
from datetime import datetime
from pint import UnitRegistry

ureg = UnitRegistry()


class UnitValue(BaseModel):
    value: float
    unit: str


class PCE(UnitValue):
    value: confloat(ge=0, le=40)
    unit: Literal["%"]


class JSC(UnitValue):
    value: float
    unit: Literal["mA cm^-2", "A m^-2", "A cm^-2", "mA m^-2", "uA cm^-2"]


class VOC(UnitValue):
    value: confloat(ge=0, le=1500)
    unit: Literal["V", "mV"]

    @validator("value")
    def check_voc_value(cls, v, values):
        unit = values.get("unit")
        if unit == "V" and v > 1.5:
            raise ValueError("When unit is 'V', value must be <= 1.5")
        elif unit == "mV" and v > 1500:
            raise ValueError("When unit is 'mV', value must be <= 1500")
        return v


class FF(BaseModel):
    value: confloat(ge=0.0, le=100) = Field(
        ...,
        description="Mostly the Fill factor is given as a percentage (%). In case is not make sure to convert it from ratio to percentage.",
    )


class ActiveArea(UnitValue):
    value: confloat(gt=0)
    unit: Literal["cm^2", "mm^2"]

    @validator("value")
    def convert_to_cm2(cls, v, values):
        unit = values.get("unit")
        if unit == "mm^2":
            return v / 100
        return v


class LightIntensity(UnitValue):
    value: confloat(ge=0)
    unit: Literal["mW cm^-2", "W m^-2", "mW m^-2", "sun", "lux"]


class Bandgap(UnitValue):
    value: confloat(ge=0.5, le=4.0)
    unit: Literal["eV"]


class Temperature(UnitValue):
    value: float
    unit: Literal["°C", "K"]

    @validator("value")
    def convert_to_celsius(cls, v, values):
        if values.get("unit") == "K":
            return v - 273.15
        return v


class Time(UnitValue):
    value: float
    unit: Literal["s", "min", "h", "days", "weeks", "months", "years"]


class PowerDensity(UnitValue):
    value: float
    unit: Literal["uW/cm^2", "mW/cm^2"]


class LightSource(BaseModel):
    type: Literal[
        "AM 1.5G", "AM 1.5D", "AM 0", "Monochromatic", "White LED", "Other", "Outdoor"
    ]
    description: Optional[str] = Field(
        None,
        description="Additional details about the light source. This is very important.",
    )
    light_intensity: LightIntensity
    lamp: Optional[str] = Field(
        None, description="Type of lamp used to generate the spectrum"
    )


class Pressure(UnitValue):
    value: float
    unit: Literal["Pa", "kPa", "atm", "bar", "mbar", "mmHg", "Torr"]


class Humidity(UnitValue):
    value: confloat(ge=0, le=100)
    unit: Literal["%"]


class Concentration(UnitValue):
    value: float
    unit: Literal["mol/L", "mmol/L", "g/L", "mg/L", "wt%", "vol%", "M"]


class Volume(UnitValue):
    value: float
    unit: Literal["L", "mL", "μL"]


class ProcessingAtmosphere(BaseModel):
    type: str
    pressure: Optional[Pressure]
    relative_humidity: Optional[Humidity]


class Solvent(BaseModel):
    name: str
    volume: Optional[Volume]


class ReactionSolution(BaseModel):
    compounds: List[str]
    concentrations: Optional[List[Concentration]]
    volume: Optional[Volume]
    temperature: Temperature
    solvent: Solvent


class ProcessingStep(BaseModel):
    step_name: Optional[str]
    method: Optional[str] = Field(
        ...,
        description="This is the method for the processing of steps in the design of the cells. Some examples are: Spin-coating, Drop-infiltration, Co-evaporation, Doctor blading, Spray coating, Slot-die coating, Ultrasonic spray, Dropcasting, Inkjet printing, Electrospraying, Thermal-annealing, Antisolvent-quenching.",
    )
    atmosphere: Optional[ProcessingAtmosphere]
    temperature: Optional[Temperature]
    duration: Optional[Time]
    antisolvent: Optional[Solvent]
    gas: Optional[str]
    solution: Optional[ReactionSolution]
    additional_parameters: Optional[dict] = Field(
        None, description="Any additional parameters specific to this processing step"
    )


class Deposition(BaseModel):
    steps: List[ProcessingStep] = Field(
        ..., min_items=1, description="List of processing steps in order of execution.  Only report conditions that have reported in the paper."
    )
    additional_notes: Optional[str] = Field(
        None, description="Any additional notes about the overall deposition process"
    )


class Stability(BaseModel):
    time: Time
    light_intensity: LightIntensity
    humidity: Humidity
    temperature: Temperature
    PCE_T80: Optional[Time] = Field(
        None,
        description="The time after which the cell performance has degraded by 20% with respect to the initial performance.",
    )
    PCE_at_the_start_of_the_experiment: Optional[PCE]
    PCE_after_1000_hours: Optional[PCE]
    PCE_at_the_end_of_description: Optional[PCE]

class Thickness(BaseModel):
    value: float
    unit: Literal["nm", "µm"]

class Layer(BaseModel):
    name: str
    thickness: Optional[Thickness] = Field(
        None, description="Total thickness of the deposited perovskite layer."
    )
    functionality: Literal[
        "Hole-transport",
        "Electron-transport",
        "Contact",
        "Absorber",
        "Other",
        "Substrate",
    ]
    deposition: Optional[Deposition]


class PerovskiteSolarCell(BaseModel):
    cell_stack: List[str] = Field(..., description="The stack sequence of the cell.")
    perovskite_composition: str = Field(
        ..., description="Chemical formula of the perovskite absorber"
    )
    device_architecture: Optional[
        Literal["pin", "nip", "Back contacted", "Front contacted"]
    ]
    pce: PCE
    jsc: JSC
    voc: VOC
    ff: FF
    number_devices: Optional[int] = Field(None, description="Over how may devices the performance metrics have been averaged.")
    averaged_quantities: bool = Field(None, description="True if the reported performance metrics are reported based on an average over multiple devices. If there are additional statistics that have been reported, extract them into `additional_notes`.")
    active_area: ActiveArea = Field(..., description='Reported active area of the solar cell.')
    light_source: LightSource
    bandgap: Optional[Bandgap] = Field(
        None,
        description="Bandgap of the perovskite material in eV. Include this field only if the bandgap has been directly measured in the experiment. Do not include estimated or literature values.",
    )
    encapsulation: Optional[str] = Field(
        None, description="Encapsulation method, if any"
    )
    additional_notes: Optional[str] = Field(
        None, description="Any additional comments or observations"
    )
    stability: Optional[Stability] = Field(None, description="Include this field only if stability tests have been performed. Only include conditions that have been explicitly reported in the paper. If there are additional statistics, report them in `additional_notes`.")
    layers: List[Layer] = Field(None, description="Include all layers in the cell stack. Only report conditions for those where deposition conditions have been reported in the paper. Include the ETL, HTL, Contact, Absorber, and Substrate layers.")

    @validator("cell_stack")
    def check_cell_stack(cls, v):
        if len(v) < 4:
            raise ValueError("Cell stack must have at least 4 layers")
        return v


class PerovskiteSolarCells(BaseModel):
    cells: List[PerovskiteSolarCell]
