from pydantic import BaseModel, Field, validator, confloat
from typing import List, Literal, Optional


class Ion(BaseModel):
    abbreviation: Optional[str] = Field(
        None,
        description="The abbreviation used for the ion when writing the perovskite composition such as: 'Cs', 'MA', 'FA', 'PEA'",
    )
    coefficient: Optional[str] = Field(
        None,
        description="The stoichiometric coefficient of the ion such as “0.75”, or “1-x”.",
    )


class PerovskiteComposition(BaseModel):
    formula: Optional[str] = Field(
        None,
        description="The perovskite composition according to IUPAC recommendations, where standard abbreviations are used for all ions.",
    )
    dimensionality: Optional[Literal["0D", "1D", "2D", "3D", "2D/3D"]] = Field(None)
    a_ions: Optional[List[Ion]] = Field(None)
    b_ions: Optional[List[Ion]] = Field(None)
    x_ions: Optional[List[Ion]] = Field(None)


class UnitValue(BaseModel):
    value: Optional[float] = Field(None)
    unit: Optional[str] = Field(None)


class PCE(UnitValue):
    value: Optional[confloat(ge=0, le=40)] = Field(None)
    unit: Optional[Literal["%"]] = Field(None)


class JSC(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["mA cm^-2", "A m^-2", "A cm^-2", "mA m^-2", "uA cm^-2"]] = (
        Field(None)
    )


class VOC(UnitValue):
    value: Optional[confloat(ge=0, le=1500)] = Field(None)
    unit: Optional[Literal["V", "mV"]] = Field(None)

    @validator("value")
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
    value: Optional[confloat(ge=0.0, le=100)] = Field(
        None,
        description="Mostly the Fill factor is given as a percentage (%). In case is not make sure to convert it from ratio to percentage.",
    )


class ActiveArea(UnitValue):
    value: Optional[confloat(gt=0)] = Field(None)
    unit: Optional[Literal["cm^2", "mm^2"]] = Field(None)

    @validator("value")
    def convert_to_cm2(cls, v, values):
        if v is None:
            return v
        unit = values.get("unit")
        if unit == "mm^2":
            return v / 100
        return v


class LightIntensity(UnitValue):
    value: Optional[confloat(ge=0)] = Field(None)
    unit: Optional[Literal["mW cm^-2", "W m^-2", "mW m^-2", "sun", "lux"]] = Field(None)


class Bandgap(UnitValue):
    value: Optional[confloat(ge=0.5, le=4.0)] = Field(None)
    unit: Optional[Literal["eV"]] = Field(None)


class Temperature(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["°C", "K"]] = Field(None)

    @validator("value")
    def convert_to_celsius(cls, v, values):
        if v is None:
            return v
        if values.get("unit") == "K":
            return v - 273.15
        return v


class Time(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["s", "min", "h", "days", "weeks", "months", "years"]] = (
        Field(None)
    )


class PowerDensity(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["uW/cm^2", "mW/cm^2"]] = Field(None)


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
    ] = Field(None)
    description: Optional[str] = Field(
        None,
        description="Additional details about the light source. This is very important.",
    )
    light_intensity: Optional[LightIntensity] = Field(None)
    lamp: Optional[str] = Field(
        None, description="Type of lamp used to generate the spectrum"
    )


class Pressure(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["Pa", "kPa", "atm", "bar", "mbar", "mmHg", "Torr"]] = Field(
        None
    )


class Humidity(UnitValue):
    value: Optional[confloat(ge=0, le=100)] = Field(None)
    unit: Optional[Literal["%"]] = Field(None)


class Concentration(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["mol/L", "mmol/L", "g/L", "mg/L", "wt%", "vol%", "M"]] = (
        Field(None)
    )


class Solute(BaseModel):
    name: Optional[str]
    concentration: Optional[Concentration]


class Volume(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["L", "mL", "μL"]] = Field(None)


class ProcessingAtmosphere(BaseModel):
    type: Optional[str] = Field(None)
    pressure: Optional[Pressure] = Field(None)
    relative_humidity: Optional[Humidity] = Field(None)


class ReactionSolution(BaseModel):
    compounds: Optional[List[str]] = Field(None)
    solutes: Optional[List[Solute]] = Field(None)
    volume: Optional[Volume] = Field(None)
    temperature: Optional[Temperature] = Field(None)
    solvent: Optional[str] = Field(None)


class ProcessingStep(BaseModel):
    step_name: Optional[str] = Field(None)
    method: Optional[str] = Field(
        None,
        description="This is the method for the processing of steps in the design of the cells. Some examples are: Spin-coating, Drop-infiltration, Co-evaporation, Doctor blading, Spray coating, Slot-die coating, Ultrasonic spray, Dropcasting, Inkjet printing, Electrospraying, Thermal-annealing, Antisolvent-quenching.",
    )
    atmosphere: Optional[ProcessingAtmosphere] = Field(None)
    temperature: Optional[Temperature] = Field(None)
    duration: Optional[Time] = Field(None)
    antisolvent: Optional[str] = Field(None)
    gas_quenching: Optional[bool] = Field(
        None, description="Whether the crystallization was induced by gas quenching"
    )
    solution: Optional[ReactionSolution] = Field(None)
    additional_parameters: Optional[dict] = Field(
        None, description="Any additional parameters specific to this processing step"
    )


class Stability(BaseModel):
    time: Optional[Time] = Field(None)
    light_intensity: Optional[LightIntensity] = Field(None)
    humidity: Optional[Humidity] = Field(None)
    temperature: Optional[Temperature] = Field(None)
    PCE_T80: Optional[Time] = Field(
        None,
        description="The time after which the cell performance has degraded by 20% with respect to the initial performance.",
    )
    PCE_at_the_start_of_the_experiment: Optional[PCE]
    PCE_after_1000_hours: Optional[PCE]
    PCE_at_the_end_of_description: Optional[PCE]


class Thickness(BaseModel):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["nm", "µm"]] = Field(None)


class Layer(BaseModel):
    name: Optional[str] = Field(None)
    thickness: Optional[Thickness] = Field(
        None, description="Total thickness of the deposited perovskite layer."
    )
    functionality: Optional[
        Literal[
            "Hole-transport",
            "Electron-transport",
            "Contact",
            "Absorber",
            "Other",
            "Substrate",
        ]
    ] = Field(
        None,
        description="""
        The functionality of the perovskite solar cell layer should be one of the following:
        - Hole-transport: Spiro-MeOTAD, PEDOT, PTAA, NiO
        - Electron-transport: TiO2, SnO2, ZnO, PCBM
        - Contact: Au, Ag, Al, MoO3, interface layers
        - Absorber: Perovskite active layers (MAPbI3, CsPbI3)
        - Substrate: FTO, ITO, glass, flexible polymers
        - Other: Antireflective, buffer layers, unclassified
    """,
    )
    deposition: Optional[List[ProcessingStep]] = Field(
        None,
        description="List of processing steps in order of execution. Only report conditions that have reported in the paper.",
    )
    additional_treatment: Optional[str] = Field(
        None,
        description="""
        Description of modifications applied to this layer beyond its basic composition, including:

        - Self-assembled monolayers (SAMs)
        - Surface passivation treatments
        - Interface engineering (e.g., Lewis base/acid treatments)
        - Additives or dopants
        - Post-deposition treatments

        Use established terminology: "SAM" for self-assembled molecular layers, "surface passivation", "doping" where applicable.
    """,
    )


class PerovskiteSolarCell(BaseModel):
    cell_stack: Optional[List[str]] = Field(
        None, description="The stack sequence of the cell."
    )
    perovskite_composition: Optional[PerovskiteComposition] = Field(None)
    device_architecture: Optional[
        Literal["pin", "nip", "Back contacted", "Front contacted"]
    ] = Field(None)
    pce: Optional[PCE] = Field(None)
    jsc: Optional[JSC] = Field(None)
    voc: Optional[VOC] = Field(None)
    ff: Optional[FF] = Field(None)
    number_devices: Optional[int] = Field(
        None,
        description="Over how may devices the performance metrics have been averaged.",
    )
    averaged_quantities: Optional[bool] = Field(
        None,
        description="True if the reported performance metrics are reported based on an average over multiple devices. If there are additional statistics that have been reported, extract them into `additional_notes`.",
    )
    active_area: Optional[ActiveArea] = Field(
        None, description="Reported active area of the solar cell."
    )
    light_source: Optional[LightSource] = Field(None)
    bandgap: Optional[Bandgap] = Field(
        None,
        description="Bandgap of the perovskite material in eV. Include this field only if the bandgap has been directly measured in the experiment. Do not include estimated or literature values.",
    )
    encapsulated: Optional[bool] = Field(
        None, description="True if the cell has been encapsulated."
    )
    additional_notes: Optional[str] = Field(
        None, description="Any additional comments or observations"
    )
    stability: Optional[Stability] = Field(
        None,
        description="Include this field only if stability tests have been performed. Only include conditions that have been explicitly reported in the paper. If there are additional statistics, report them in `additional_notes`.",
    )
    layers: Optional[List[Layer]] = Field(
        None,
        description="Include all layers in the cell stack. Only report conditions for those where deposition conditions have been reported in the paper. Include the ETL, HTL, Contact, Absorber, and Substrate layers.",
    )

    @validator("cell_stack")
    def check_cell_stack(cls, v):
        if v is not None and len(v) < 4:
            raise ValueError("Cell stack must have at least 4 layers")
        return v


class PerovskiteSolarCells(BaseModel):
    cells: Optional[List[PerovskiteSolarCell]] = Field(None)
