from pydantic import BaseModel, Field, validator, confloat
from typing import List, Literal, Optional


class Ion(BaseModel):
    abbreviation: Optional[str] = Field(
        None,
        description="The abbreviation used for the ion when writing the perovskite composition such as: 'Cs', 'MA', 'FA', 'PEA'",
    )

    common_name: Optional[str] = Field(
        None,
        description="The common name of the ion such as 'Cesium', 'Methylammonium', 'Formamidinium', 'Phenylethylammonium'",
    )

    molecular_formula: Optional[str] = Field(
        None,
        description="The molecular formula of the ion such as 'Cs+', 'CH5N+', 'CH6N+', 'C10H15N2+'",
    )

    coefficient: Optional[str] = Field(
        None,
        description="The stoichiometric coefficient of the ion such as “0.75”, or “1-x”. You can break it down from the perovskite formula. For example: FA0.85MA0.15PbI2.55Br0.45 will fill 0.85 coefficient for FA, 0.15 for MA, 1 for Pb, and 2 for I.",
    )


class UnitValue(BaseModel):
    value: Optional[float] = Field(None)
    unit: Optional[str] = Field(None)


class Bandgap(UnitValue):
    value: Optional[confloat(ge=0.5, le=4.0)] = Field(None)
    unit: Optional[Literal["eV"]] = Field(None)


class Concentration(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[
        Literal["mol/L", "mmol/L", "g/L", "mg/L", "mg/mL", "wt%", "vol%", "M"]
    ] = Field(None)


class Impurity(BaseModel):
    abbreviation: Optional[str] = Field(
        default=None, description="The abbreviation used for the additive or impurity."
    )
    concentration: Optional[Concentration] = Field(
        default=None,
        description="The concentration of the additive or impurity. (unit: cm^-3)",
    )


class PerovskiteComposition(BaseModel):
    formula: Optional[str] = Field(
        None,
        description="The perovskite composition according to IUPAC recommendations, where standard abbreviations are used for all ions.",
    )
    sample_type: Optional[
        Literal[
            "Polycrystalline film",
            "Single crystal",
            "Quantum dots",
            "Nano rods",
            "Colloidal solution",
            "Amorphous",
            "Other",
        ]
    ] = Field(
        None,
        description="Type of the perovskite (e.g., Polycrystalline film, Single crystal, etc.).",
    )
    dimensionality: Optional[Literal["0D", "1D", "2D", "3D", "2D/3D"]] = Field(None)
    a_ions: Optional[List[Ion]] = Field(
        None,
        description="A-site ions. Only include information that is described in the paper.",
    )
    b_ions: Optional[List[Ion]] = Field(
        None,
        description="B-site ions. Only include information that is described in the paper.",
    )
    x_ions: Optional[List[Ion]] = Field(
        None,
        description="X-site ions. Only include information that is described in the paper.",
    )
    # bandgap: Optional[Bandgap] = Field(
    #     None,
    #     description="Bandgap of the perovskite material in eV. Include this field only if the bandgap has been directly measured in the experiment. Do not include estimated or literature values.",
    # )
    bandgap: Optional[Bandgap] = Field(
        None,
        description="Bandgap of the perovskite material being used in eV. You can also estimate the bandgap based on your knowledge if it's not mentioned in the paper.",
    )
    impurities: Optional[Impurity] = Field(
        None, description="List any impurities added to the perovskite layer."
    )
    additives: Optional[Impurity] = Field(
        None, description="List any additives added to the perovskite layer."
    )


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
    unit: Optional[Literal["Pa", "kPa", "atm", "bar", "mbar", "mmHg", "torr"]] = Field(
        None
    )


class Humidity(UnitValue):
    value: Optional[confloat(ge=0, le=100)] = Field(None)
    unit: Optional[Literal["%"]] = Field(None)


class Solute(BaseModel):
    name: Optional[str]
    concentration: Optional[Concentration]


class Volume(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["L", "mL", "μL"]] = Field(None)


class Solvent(BaseModel):
    name: Optional[str] = Field(None)
    volume_fraction: Optional[float] = Field(
        None,
        description="The volume fraction of the solvent with respect to the other solvents in the solution",
    )


class ReactionSolution(BaseModel):
    compounds: Optional[List[str]] = Field(None)
    solutes: Optional[List[Solute]] = Field(None)
    volume: Optional[Volume] = Field(
        None,
        description="This volume is the volume of solution used in the experiment, e.g. the solvent volume that is spin-coated rather than the volume of the stock solution.",
    )
    temperature: Optional[Temperature] = Field(None)
    solvents: Optional[List[Solvent]] = Field(None)


class ProcessingStep(BaseModel):
    step_name: Optional[str] = Field(None)
    method: Optional[str] = Field(
        None,
        description="This is the method for the processing of steps in the design of the cells. Some examples are: Spin-coating, Drop-infiltration, Evaporation, Co-evaporation, Doctor blading, Spray coating, Slot-die coating, Ultrasonic spray, Dropcasting, Inkjet printing, Electrospraying, Thermal-annealing, Antisolvent-quenching, Gas quenching.",
    )
    atmosphere: Optional[
        Literal[
            "Ambient air", "Dry air", "Air", "N2", "Ar", "He", "H2", "Vacuum", "Other"
        ]
    ]
    temperature: Optional[Temperature] = Field(None)
    duration: Optional[Time] = Field(None)
    antisolvent: Optional[str] = Field(None)
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
    PCE_at_the_end_of_experiment: Optional[
        PCE
    ]  # TODO: We should have percentage of original PCE as that is more reported in the papers. Also keep a field for the value at the end of the stability test. Also add original_pce or pce_at_start like field with actual value.
    potential_bias: Optional[
        Literal[
            "Open circuit",
            "MPPT",
            "Constant potential",
            "Constant current",
            "Constant resistance",
        ]
    ]


class Thickness(BaseModel):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["nm", "µm"]] = Field(None)


class Layer(BaseModel):
    name: Optional[str] = Field(
        None,
        description="Name of the material in the layer. Use standard abbreviations if possible. If the layer has additional modificatons done to it, include them here. For example, CH3NH3PbI3 w/ PEG or CH3NH3PbI3 w/ PCBM.",
    )
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
    perovskite_composition: Optional[PerovskiteComposition] = Field(None)
    device_architecture: Optional[
        Literal["pin", "nip", "Back contacted", "Front contacted", "Other"]
    ] = Field(None)
    pce: Optional[PCE] = Field(None)
    jsc: Optional[JSC] = Field(None)
    voc: Optional[VOC] = Field(None)
    ff: Optional[FF] = Field(None)
    number_devices: Optional[int] = Field(
        None,
        description="Over how many devices the performance metrics have been averaged.",
    )
    averaged_quantities: Optional[bool] = Field(
        None,
        description="True if the reported performance metrics are reported based on an average over multiple devices. If there are additional statistics that have been reported, extract them into `additional_notes`.",
    )
    active_area: Optional[ActiveArea] = Field(
        None, description="Reported active area of the solar cell."
    )
    light_source: Optional[LightSource] = Field(None)
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


class PerovskiteSolarCells(BaseModel):
    cells: Optional[List[PerovskiteSolarCell]] = Field(None)
