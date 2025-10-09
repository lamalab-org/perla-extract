from pint import UnitRegistry

# Initialize the UnitRegistry
ureg = UnitRegistry()

ureg.define("sun = 1 kW/m^2")
ureg.define("vol = 1 m^3")

pint = {
    "default_units_by_type": {
        ureg.percent.dimensionality: (ureg.percent, "%"),  # Efficiency, humidity, etc.
        (ureg.ampere / (ureg.centimeter**2)).dimensionality: (
            "mA cm^-2",
            "mA cm^-2",
        ),  # Current density
        ureg.volt.dimensionality: (ureg.volt, "V"),  # Voltage
        ureg.nanometer.dimensionality: (ureg.nanometer, "nm"),  # Thickness,
        (ureg.meter**2).dimensionality: ("cm^2", "cm^2"),
        ureg.day.dimensionality: (
            ureg.second,
            "s",
        ),  # Time (converted to hours for finer granularity)
        ureg.celsius.dimensionality: (
            ureg.celsius,
            "°C",
        ),  # Temperature converted from Celsius
        (1 * ureg.mg / ureg.mL).dimensionality: (ureg.mg / ureg.mL, "mg/mL"),
        (ureg.mW / ureg.cm**2).dimensionality: ((ureg.mW / ureg.cm**2), "mW cm^-2"),
        (ureg.mW / ureg.cm**2).dimensionality: ((ureg.mW / ureg.cm**2), "mW cm^-2"),
        (ureg.mol / ureg.L).dimensionality: ((ureg.mol / ureg.L), "mol/L"),
        (ureg.eV).dimensionality: ((ureg.eV), "eV"),
        (ureg.meter**3).dimensionality: (ureg.milliliter, "mL"),
    }
}

default_units = {
    "thickness": "nm",
    "light_intensity": "mW cm^-2",
    "duration": "s",
    "temperature": "°C",
    "time": "h",
    "PCE_after_1000_hours": "%",
    "humidity": "%",
    "PCE_at_the_start_of_the_experiment": "%",
    "PCE_at_the_end_of_description": "%",
    "PCE_T80": "%",
    "bandgap": "eV",
    "concentration": "mol/L",
    "volume": "mL",
}
