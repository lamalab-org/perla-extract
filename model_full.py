from pydantic import BaseModel, Field, confloat, validator, field_validator
from typing import List, Literal, Optional, Union
from datetime import datetime, date
from enum import Enum

class CellDefinition(BaseModel):
    stack_sequence: str = Field("Unknown", description="The stack sequence describing the cell")
    area_total: float = Field(None, description="The total cell area in cm^2")
    area_measured: float = Field(None, description="The effective area of the cell during IV and stability measurements")
    number_of_cells_per_substrate: int = Field(0, description="The number of individual solar cells on the substrate")
    architecture: str = Field("Unknown", description="The cell architecture (e.g., nip, pin)")
    flexible: bool = Field(False, description="TRUE if the cell is flexible and bendable")
    flexible_minimum_bending_radius: float = Field(None, description="The minimum bending radius possible without degrading performance")
    semitransparent: bool = Field(False, description="TRUE if the cell is semi-transparent")
    semitransparent_average_visible_transmittance: float = Field(None, description="The average visible transmittance in %")
    semitransparent_transmittance_wavelength_range: str = Field(None, description="The wavelength range for transmittance measurement")

class ModuleDefinition(BaseModel):
    is_module: bool = Field(False, description="TRUE if the cell is a module")
    number_of_cells_in_module: int = Field(0, description="The number of cells in the module")
    area_total: float = Field(None, description="The total area of the module in cm^2")
    area_effective: float = Field(None, description="The active area of the module in cm^2")
    jv_data_recalculated_per_cell: bool = Field(False, description="TRUE if IV data is recalculated to average data per sub-cells")

class Substrate(BaseModel):
    stack_sequence: str = Field("Unknown", description="The stack sequence describing the substrate")
    thickness: str = Field(None, description="A list of thicknesses of the individual layers in the stack")
    area: float = Field(None, description="The total area in cm^2 of the substrate")
    supplier: str = Field("", description="The supplier of the substrate")
    brand_name: str = Field("", description="The specific brand name of the substrate")
    deposition_procedure: str = Field("Unknown", description="A list of the deposition procedures for the substrate")
    surface_roughness_rms: float = Field(None, description="The root mean square value of the surface roughness in nm")
    etching_procedure: str = Field("", description="The method by which the conductive layer was removed")
    cleaning_procedure: str = Field("", description="The schematic cleaning sequence of the substrate")

class ETL(BaseModel):
    stack_sequence: str = Field("Unknown", description="The stack sequence describing the electron transport layer")
    thickness: str = Field(None, description="A list of thicknesses of the individual layers in the stack")
    additives_compounds: str = Field(None, description="List of the dopants and additives in each layer")
    additives_concentrations: str = Field(None, description="The concentration of the dopants/additives")
    deposition_procedure: str = Field("Unknown", description="The deposition procedures for the ETL stack")
    deposition_aggregation_state_of_reactants: str = Field("Unknown", description="The physical state of the reactants")
    deposition_synthesis_atmosphere: str = Field("Unknown", description="The synthesis atmosphere")
    deposition_synthesis_atmosphere_pressure_total: str = Field(None, description="The total gas pressure during each reaction step")
    deposition_synthesis_atmosphere_pressure_partial: str = Field(None, description="The partial pressures for the gases present during each reaction step")
    deposition_synthesis_atmosphere_relative_humidity: str = Field(None, description="The relative humidity during each deposition step")
    storage_time_until_next_step: float = Field(None, description="The time between ETL stack finalization and next layer deposition")
    storage_atmosphere: str = Field("Unknown", description="The atmosphere during storage")
    storage_relative_humidity: float = Field(None, description="The relative humidity during storage")

class Perovskite(BaseModel):
    class Dimensionality(BaseModel):
        single_crystal: bool = Field(False, description="TRUE if the cell is based on a perovskite single crystal")
        quantum_dot: bool = Field(False, description="TRUE if the cell is based on perovskite quantum dots")
        two_dimensional: bool = Field(False, description="TRUE if the cell is based on 2D perovskites")
        two_three_dimensional_mixture: bool = Field(False, description="TRUE if the cell is based on a mixture of 2D and 3D perovskites")
        three_dimensional: bool = Field(False, description="TRUE for standard three-dimensional perovskites")
        three_dimensional_with_two_dimensional_capping: bool = Field(False, description="TRUE if 3D with a thin 2D-capping layer")
        dimensionality_list: str = Field(None, description="A list of the perovskite dimensionalities")

    class Composition(BaseModel):
        perovskite_abc3_structure: bool = Field(False, description="TRUE if the photo-absorber has a perovskite structure")
        perovskite_inspired_structure: bool = Field(False, description="TRUE if the photo absorber does not have a perovskite structure")
        a_ions: str = Field(None, description="List of the A-site ions in the perovskite structure")
        a_ions_coefficients: str = Field(None, description="A list of the perovskite coefficients for the A-site ions")
        b_ions: str = Field(None, description="List of the B-site ions in the perovskite structure")
        b_ions_coefficients: str = Field(None, description="A list of the perovskite coefficients for the B-site ions")
        c_ions: str = Field(None, description="List of the C-site ions in the perovskite structure")
        c_ions_coefficients: str = Field(None, description="A list of the perovskite coefficients for the C-site ions")
        non_stoichiometry_components_in_excess: str = Field(None, description="Components that are in excess in the perovskite synthesis")
        short_form: str = Field("Unknown", description="The perovskite composition written in shorthand notation")
        long_form: str = Field("Unknown", description="The perovskite composition written in long form")
        composition_assumption: str = Field(None, description="The knowledge base from which the perovskite composition is inferred")
        inorganic_perovskite: bool = Field(False, description="TRUE if the perovskite does not contain any organic ions")
        lead_free: bool = Field(False, description="TRUE if the perovskite is completely lead free")

    dimensionality: Dimensionality
    composition: Composition
    additives_compounds: str = Field(None, description="List of the dopants and additives in the perovskite")
    additives_concentrations: str = Field(None, description="The concentration of the dopants/additives")
    thickness: str = Field(None, description="The thickness of the perovskite layer")
    band_gap: str = Field(None, description="The band gap of the perovskite")
    band_gap_graded: str = Field(None, description="TRUE if the band gap varies as a function of the vertical position")
    band_gap_estimation_basis: str = Field(None, description="The method by which the band gap was estimated")
    pl_max: str = Field(None, description="The maximum from steady-state PL measurements")

class PerovskiteDeposition(BaseModel):
    number_of_deposition_steps: int = Field(0, description="The number of production steps involved in making the perovskite-stack")
    deposition_procedure: str = Field("Unknown", description="The deposition procedures for the perovskite block")
    deposition_aggregation_state_of_reactants: str = Field("Unknown", description="The physical state of the reactants")
    deposition_synthesis_atmosphere: str = Field("Unknown", description="The synthesis atmosphere")
    deposition_synthesis_atmosphere_pressure_total: str = Field(None, description="The total gas pressure during each reaction step")
    deposition_synthesis_atmosphere_pressure_partial: str = Field(None, description="The partial pressures for the gases present during each reaction step")
    deposition_synthesis_atmosphere_relative_humidity: str = Field(None, description="The relative humidity during each deposition step")
    quenching_induced_crystallisation: bool = Field(False, description="TRUE if measures were taken to discontinuously accelerate the crystallisation process")
    quenching_media: str = Field(None, description="The solvents used in the antisolvent treatment")
    quenching_media_mixing_ratios: str = Field(None, description="The mixing ratios of the antisolvent")
    quenching_media_volume: float = Field(None, description="The volume of the antisolvent")
    thermal_annealing_temperature: str = Field(None, description="The temperatures of the thermal annealing program")
    thermal_annealing_time: str = Field(None, description="The time program associated with the thermal annealing")
    thermal_annealing_atmosphere: str = Field(None, description="The atmosphere during thermal annealing")
    solvent_annealing: bool = Field(False, description="TRUE if there has been a separate solvent annealing step")
    storage_time_until_next_step: float = Field(None, description="The time between perovskite stack finalization and next layer deposition")
    storage_atmosphere: str = Field("Unknown", description="The atmosphere during storage")
    storage_relative_humidity: float = Field(None, description="The relative humidity during storage")

class HTL(BaseModel):
    stack_sequence: str = Field("Unknown", description="The stack sequence describing the hole transport layer")
    thickness: str = Field(None, description="A list of thicknesses of the individual layers in the stack")
    additives_compounds: str = Field(None, description="List of the dopants and additives in each layer")
    additives_concentrations: str = Field(None, description="The concentration of the dopants/additives")
    deposition_procedure: str = Field("Unknown", description="The deposition procedures for the HTL stack")
    deposition_synthesis_atmosphere: str = Field("Unknown", description="The synthesis atmosphere")
    deposition_synthesis_atmosphere_pressure_total: str = Field(None, description="The total gas pressure during each reaction step")
    deposition_synthesis_atmosphere_pressure_partial: str = Field(None, description="The partial pressures for the gases present during each reaction step")
    deposition_synthesis_atmosphere_relative_humidity: str = Field(None, description="The relative humidity during each deposition step")
    storage_time_until_next_step: float = Field(None, description="The time between HTL stack finalization and next layer deposition")
    storage_atmosphere: str = Field("Unknown", description="The atmosphere during storage")
    storage_relative_humidity: float = Field(None, description="The relative humidity during storage")

class BackContact(BaseModel):
    stack_sequence: str = Field("Unknown", description="The stack sequence describing the back contact")
    thickness: str = Field(None, description="A list of thicknesses of the individual layers in the stack")
    additives_compounds: str = Field(None, description="List of the dopants and additives in each layer")
    additives_concentrations: str = Field(None, description="The concentration of the dopants/additives")
    deposition_procedure: str = Field("Unknown", description="The deposition procedures for the back contact")
    deposition_synthesis_atmosphere: str = Field("Unknown", description="The synthesis atmosphere")
    deposition_synthesis_atmosphere_pressure_total: str = Field(None, description="The total gas pressure during each reaction step")
    deposition_synthesis_atmosphere_pressure_partial: str = Field(None, description="The partial pressures for the gases present during each reaction step")
    deposition_synthesis_atmosphere_relative_humidity: str = Field(None, description="The relative humidity during each deposition step")
    storage_time_until_next_step: float = Field(None, description="The time between back contact finalization and next step")
    storage_atmosphere: str = Field("Unknown", description="The atmosphere during storage")
    storage_relative_humidity: float = Field(None, description="The relative humidity during storage")

class AdditionalLayers(BaseModel):
    frontside: bool = Field(False, description="TRUE if there is a functional layer below the substrate")
    frontside_function: str = Field("", description="The function of the additional layers on the substrate side")
    frontside_stack_sequence: str = Field("Unknown", description="The stack sequence describing the additional layers on the substrate side")
    frontside_thickness: str = Field(None, description="A list of thicknesses of the individual layers in the stack")
    frontside_deposition_procedure: str = Field("Unknown", description="The deposition procedures for the additional layers")

class Encapsulation(BaseModel):
    is_encapsulated: bool = Field(False, description="TRUE if the cell is encapsulated")
    stack_sequence: str = Field("Unknown", description="The stack sequence of the encapsulation")
    edge_sealing_materials: str = Field("", description="Edge sealing materials")
    atmosphere_for_encapsulation: str = Field("Unknown", description="The surrounding atmosphere during encapsulation")
    water_vapour_transmission_rate: float = Field(None, description="The water vapour transmission rate through the encapsulation")
    oxygen_transmission_rate: float = Field(None, description="The oxygen transmission rate through the encapsulation")

class JVData(BaseModel):
    measured: bool = Field(False, description="TRUE if IV-data has been measured and is reported")
    average_over_n_cells: int = Field(0, description="The number of cells the reported IV data is based on")
    certified_values: bool = Field(False, description="TRUE if the IV data is measured by an independent certification institute")
    certification_institute: str = Field("", description="The name of the certification institute")
    storage_age_of_cell: float = Field(None, description="The age of the cell in days")
    storage_atmosphere: str = Field("Unknown", description="The atmosphere in which the sample was stored")
    storage_relative_humidity: str = Field(None, description="The relative humidity during storage")
    test_atmosphere: str = Field("Unknown", description="The atmosphere in which the IV measurement is conducted")
    test_relative_humidity: float = Field(None, description="The relative humidity during the IV measurement")
    test_temperature: float = Field(None, description="The temperature of the device during the IV-measurement")
    light_source_type: str = Field("", description="The type of light source used during the IV-measurement")
    light_source_brand_name: str = Field("", description="The brand name and model number of the light source/solar simulator")
    light_source_simulator_class: str = Field("", description="The class of the solar simulator")
    light_intensity: float = Field(None, description="The light intensity during the IV measurement")
    light_spectrum: str = Field("", description="The light spectrum used during the IV measurement")
    light_wavelength_range: str = Field("", description="The wavelength range of the light source")
    reverse_scan_voc: float = Field(None, description="The open circuit potential, Voc, at the reverse voltage sweep")
    reverse_scan_jsc: float = Field(None, description="The short circuit current, Jsc, at the reverse voltage sweep")
    reverse_scan_ff: float = Field(None, description="The fill factor, FF, at the reverse voltage sweep")
    reverse_scan_pce: float = Field(None, description="The efficiency, PCE, at the reverse voltage sweep")
    reverse_scan_vmp: float = Field(None, description="The potential at the maximum power point, Vmp, at the reverse voltage sweep")
    reverse_scan_jmp: float = Field(None, description="The current density at the maximum power point, Jmp, at the reverse voltage sweep")
    reverse_scan_series_resistance: float = Field(None, description="The series resistance as extracted from the reverse voltage sweep")
    reverse_scan_shunt_resistance: float = Field(None, description="The shunt resistance as extracted from the reverse voltage sweep")
    forward_scan_voc: float = Field(None, description="The open circuit potential, Voc, at the forward voltage sweep")
    forward_scan_jsc: float = Field(None, description="The short circuit current, Jsc, at the forward voltage sweep")
    forward_scan_ff: float = Field(None, description="The fill factor, FF, at the forward voltage sweep")
    forward_scan_pce: float = Field(None, description="The efficiency, PCE, at the forward voltage sweep")
    forward_scan_vmp: float = Field(None, description="The potential at the maximum power point, Vmp, at the forward voltage sweep")
    forward_scan_jmp: float = Field(None, description="The current density at the maximum power point, Jmp, at the forward voltage sweep")
    forward_scan_series_resistance: float = Field(None, description="The series resistance as extracted from the forward voltage sweep")
    forward_scan_shunt_resistance: float = Field(None, description="The shunt resistance as extracted from the forward voltage sweep")
    link_raw_data: str = Field("", description="A link to where the data file for the IV-data is stored")

class StabilizedEfficiencies(BaseModel):
    measured: bool = Field(False, description="TRUE if a stabilized cell efficiency has been measured")
    procedure: str = Field("", description="The Potentiostatic load condition during the stabilized performance measurement")
    procedure_metrics: str = Field("", description="The metrics associated with the load condition")
    measurement_time: float = Field(None, description="The duration of the stabilized performance measurement in minutes")
    pce: float = Field(None, description="The stabilized efficiency, PCE, in %")
    vmp: float = Field(None, description="The stabilized Vmp in volts")
    jmp: float = Field(None, description="The stabilized Jmp in mA/cm^2")
    link_raw_data: str = Field("", description="A link to where the data file for the stabilized performance measurement is stored")

class QuantumEfficiency(BaseModel):
    measured: bool = Field(False, description="TRUE if the external quantum efficiency has been measured")
    light_bias: float = Field(None, description="The light intensity of any bias light during the EQE measurement")
    integrated_jsc: float = Field(None, description="The integrated current from the EQE measurement")
    link_raw_data: str = Field("", description="A link to where the data file for the EQE measurement is stored")

class Stability(BaseModel):
    measured: bool = Field(False, description="TRUE if some kind of stability measurement has been done")
    protocol: str = Field("", description="The stability protocol used for the stability measurement")
    average_over_n_cells: int = Field(1, description="The number of cells the reported stability data is based on")
    light_source_type: str = Field("", description="The type of light source used during the stability measurement")
    light_intensity: float = Field(None, description="The light intensity during the stability measurement")
    light_spectra: str = Field("", description="The light spectrum used during the stability measurement")
    light_wavelength_range: str = Field("", description="The wavelength range of the light source")
    light_illumination_direction: str = Field("", description="The direction of the illumination with respect to the device stack")
    light_load_condition: str = Field("", description="The load situation of the illumination during the stability measurement")
    potential_bias_load_condition: str = Field("", description="The Potentiostatic load condition during the stability measurement")
    potential_bias_range: str = Field("", description="The potential range during the stability measurement")
    temperature_load_condition: str = Field("", description="The load situation of the temperature during the stability measurement")
    temperature_range: str = Field("", description="The temperature range during the stability measurement")
    atmosphere: str = Field("Unknown", description="The atmosphere in which the stability measurement is conducted")
    relative_humidity_load_condition: str = Field("", description="The load situation of the relative humidity during the stability measurement")
    relative_humidity_range: str = Field("", description="The relative humidity range during the stability measurement")
    total_exposure_time: float = Field(None, description="The total duration of the stability measurement in hours")
    pce_initial_value: float = Field(None, description="The efficiency, PCE, of the cell before the stability measurement routine starts")
    pce_end_of_experiment: float = Field(None, description="The efficiency, PCE, of the cell at the end of the stability routine")
    t80: float = Field(None, description="The time after which the cell performance has degraded by 20% with respect to the initial performance")
    link_raw_data: str = Field("", description="A link to where the data file for the stability data is stored")

class OutdoorTesting(BaseModel):
    tested: bool = Field(False, description="TRUE if the performance of the cell has been tested outdoors")
    protocol: str = Field("", description="The protocol used for the outdoor testing")
    average_over_n_cells: int = Field(1, description="The number of cells the reported outdoor data is based on")
    location_country: str = Field("", description="The country where the outdoor testing was occurring")
    location_city: str = Field("", description="The city where the outdoor testing was occurring")
    location_coordinates: str = Field("nan; nan", description="The coordinates for the places where the outdoor testing was occurring")
    location_climate_zone: str = Field("", description="The climate zone for the places where the outdoor testing was occurring")
    installation_tilt: float = Field(None, description="The tilt of the installed solar cell")
    installation_cardinal_direction: float = Field(None, description="The cardinal direction of the installed solar cell")
    time_season: str = Field("", description="The time of year the outdoor testing was occurring")
    time_start: datetime = Field(None, description="The starting time for the outdoor measurement")
    time_end: datetime = Field(None, description="The ending time for the outdoor measurement")
    total_exposure_time: float = Field(None, description="The total duration of the outdoor measurement in days")
    potential_bias_load_condition: str = Field("", description="The Potentiostatic load condition during the outdoor measurement")
    temperature_load_condition: str = Field("", description="The load situation of the temperature during the outdoor measurement")
    temperature_range: str = Field("", description="The temperature range during the outdoor measurement")
    pce_initial_value: float = Field(None, description="The efficiency, PCE, of the cell before the measurement routine starts")
    pce_end_of_experiment: float = Field(None, description="The efficiency, PCE, of the cell at the end of the experiment")
    power_generated: float = Field(None, description="The yearly power generated during the measurement period")
    link_raw_data: str = Field("", description="A link to where the data file for the outdoor measurement is stored")

class PerovskiteSolarCell(BaseModel):
    cell_definition: CellDefinition
    module_definition: Optional[ModuleDefinition]
    substrate: Substrate
    etl: ETL
    perovskite: Perovskite
    perovskite_deposition: PerovskiteDeposition
    htl: HTL
    back_contact: BackContact
    additional_layers: Optional[AdditionalLayers]
    encapsulation: Optional[Encapsulation]
    jv_data: JVData
    stabilized_efficiencies: Optional[StabilizedEfficiencies]
    quantum_efficiency: Optional[QuantumEfficiency]
    stability: Optional[Stability]
    outdoor_testing: Optional[OutdoorTesting]
    additional_notes: Optional[str] = Field(None, description="Any additional comments, observations, or notes about the device or experiment")