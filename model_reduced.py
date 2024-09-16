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
    value: confloat(ge=7, le=40)
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
    value: confloat(ge=50, le=200)
    unit: Literal['mW cm^-2', 'W m^-2', 'mW m^-2', 'sun']

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

class PowerDensity(UnitValue):
    value: float
    unit: Literal['uW/cm^2', 'mW/cm^2']

class LightSource(BaseModel):
    type: Literal['AM 1.5G', 'AM 1.5D', 'AM 0', 'Monochromatic', 'White LED', 'Other']
    description: Optional[str] = Field(None, description="Additional details about the light source")
    wavelength_range: Optional[str] = Field(None, description="Wavelength range of the spectrum, e.g., '400-1100 nm'")
    light_intensity: LightIntensity
    lamp: Optional[str] = Field(None, description="Type of lamp used to generate the spectrum")
    color_temperature: Optional[str] = Field(None, description="Color temperature of the spectrum in K")
    power_density: Optional[PowerDensity] = Field(None, description="Power density of the light source")

class PerovskiteSolarCell(BaseModel):
    cell_stack: List[str] = Field(..., description="The stack sequence of the cell. For the perovskite, only include 'perovskite' and list the composition in the 'perovskite_composition' field")
    perovskite_composition: str = Field(..., description="Chemical formula of the perovskite absorber")
    pce: PCE
    jsc: JSC
    voc: VOC
    ff: FF
    active_area: ActiveArea
    light_source: LightSource
    bandgap: Bandgap
    substrate: str
    electron_transport_layer: str
    hole_transport_layer: str
    back_contact: str
    deposition_method: str = Field(..., description="Method used for perovskite deposition")
    annealing_temperature: Temperature
    annealing_time: float = Field(..., description="Annealing time in minutes")
    
    encapsulation: Optional[str] = Field(None, description="Encapsulation method, if any")
    
    additional_notes: Optional[str] = Field(None, description="Any additional comments or observations")

    @validator('cell_stack')
    def check_cell_stack(cls, v):
        if len(v) < 4:
            raise ValueError("Cell stack must have at least 4 layers")
        return v

class PerovskiteSolarCells(BaseModel):
    cells: List[PerovskiteSolarCell]