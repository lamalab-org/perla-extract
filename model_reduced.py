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
    unit: Literal['%']

class JSC(UnitValue):
    value: confloat(ge=0)
    unit: Literal['mA cm^-2', 'A m^-2', 'A cm^-2', 'mA m^-2', 'uA cm^-2']

class VOC(UnitValue):
    value: confloat(ge=0, le=1500)
    unit: Literal['V', 'mV']

    @validator('value')
    def check_voc_value(cls, v, values):

        unit = values.get('unit')
        if unit == 'V' and v > 1.5:
            raise ValueError("When unit is 'V', value must be <= 1.5")
        elif unit == 'mV' and v > 1500:
            raise ValueError("When unit is 'mV', value must be <= 1500")
        return v

class FF(BaseModel):
    value: confloat(ge=0.0, le=1.0)

class ActiveArea(UnitValue):
    value: confloat(gt=0)
    unit: Literal['cm^2', 'mm^2', 'm^2']

    @validator('value')
    def convert_to_cm2(cls, v, values):
        unit = values.get('unit')
        if unit == 'mm^2':
            return v / 100
        elif unit == 'm^2':
            return v * 10000
        return v

class LightIntensity(UnitValue):
    value: confloat(ge=0)
    unit: Literal['mW cm^-2', 'W m^-2', 'mW m^-2', 'sun', 'lux']

class Bandgap(UnitValue):
    value: confloat(ge=0.5, le=4.0)
    unit: Literal['eV']

class Temperature(UnitValue):
    value: float
    unit: Literal['°C', 'K']

    @validator('value')
    def convert_to_celsius(cls, v, values):
        if values.get('unit') == 'K':
            return v - 273.15
        return v

class Time(UnitValue):
    value: float
    unit: Literal['s', 'min', 'h']

class PowerDensity(UnitValue):
    value: float
    unit: Literal['uW/cm^2', 'mW/cm^2']

class LightSource(BaseModel):
    type: Literal['AM 1.5G', 'AM 1.5D', 'AM 0', 'Monochromatic', 'White LED', 'Other', 'Outdoor']
    description: Optional[str] = Field(None, description="Additional details about the light source. This is very important.")
    wavelength_range: Optional[str] = Field(None, description="Wavelength range of the spectrum, e.g., '400-1100 nm'")
    light_intensity: LightIntensity
    lamp: Optional[str] = Field(None, description="Type of lamp used to generate the spectrum")
    color_temperature: Optional[Temperature] = Field(None, description="Color temperature of the spectrum in K")
    power_density: Optional[PowerDensity] = Field(None, description="Power density of the light source")


class Pressure(UnitValue):
    value: float
    unit: Literal['Pa', 'kPa', 'atm', 'bar', 'mbar', 'mmHg', 'Torr']

class Humidity(UnitValue):
    value: confloat(ge=0, le=100)
    unit: Literal['%']

class Concentration(UnitValue):
    value: float
    unit: Literal['mol/L', 'mmol/L', 'g/L', 'mg/L', 'wt%', 'vol%']

class Volume(UnitValue):
    value: float
    unit: Literal['L', 'mL', 'μL']

class ProcessingAtmosphere(BaseModel):
    type: str
    pressure: Pressure
    relative_humidity: Optional[Humidity]

class ReactionSolution(BaseModel):
    compounds: List[str]
    concentrations: List[Concentration]
    volume: Volume
    temperature: Temperature

class QuenchingProcess(BaseModel):
    induced_crystallisation: bool
    media_mixing_ratios: Optional[List[float]]
    media_volume: Optional[Volume]
    media_additives_compounds: Optional[List[str]]
    media_additives_concentrations: Optional[List[Concentration]]

class ThermalAnnealing(BaseModel):
    temperature: Temperature
    time: Time
    atmosphere: ProcessingAtmosphere

class SolventAnnealing(BaseModel):
    solvent_atmosphere: str
    time: Time
    temperature: Temperature

class ProcessingStep(BaseModel):
    step_name: Optional[str]
    method: Optional[str]
    atmosphere: Optional[ProcessingAtmosphere]
    temperature: Optional[Temperature]
    duration: Optional[Time]
    solution: Optional[ReactionSolution]
    thermal_annealing: Optional[ThermalAnnealing]
    solvent_annealing: Optional[SolventAnnealing]
    quenching: Optional[QuenchingProcess]
    additional_parameters: Optional[dict] = Field(None, description="Any additional parameters specific to this processing step")

class Deposition(BaseModel):
    steps: List[ProcessingStep] = Field(..., min_items=1, description="List of processing steps in order of execution")
    substrate: str
    substrate_cleaning: Optional[str]
    total_thickness: Optional[float] = Field(None, description="Total thickness of the deposited perovskite layer in nm")
    additional_notes: Optional[str] = Field(None, description="Any additional notes about the overall deposition process")

class PerovskiteSolarCell(BaseModel):
    cell_stack: List[str] = Field(..., description="The stack sequence of the cell. For the perovskite, only include 'perovskite' and list the composition in the 'perovskite_composition' field")
    perovskite_composition: str = Field(..., description="Chemical formula of the perovskite absorber")
    pce: PCE
    jsc: JSC
    voc: VOC
    ff: FF
    active_area: ActiveArea
    light_source: LightSource
    bandgap: Optional[Bandgap] = Field(None, description="Bandgap of the perovskite material in eV. Include this field only if the bandgap has been directly measured in the experiment. Do not include estimated or literature values.")
    electron_transport_layer: str
    hole_transport_layer: str
    back_contact: str
    deposition: Deposition
    encapsulation: Optional[str] = Field(None, description="Encapsulation method, if any")
    additional_notes: Optional[str] = Field(None, description="Any additional comments or observations")

    @validator('cell_stack')
    def check_cell_stack(cls, v):
        if len(v) < 4:
            raise ValueError("Cell stack must have at least 4 layers")
        return v

class PerovskiteSolarCells(BaseModel):
    cells: List[PerovskiteSolarCell]
