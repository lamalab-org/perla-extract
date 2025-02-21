from pint import UnitRegistry

# Initialize the UnitRegistry
ureg = UnitRegistry()

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
    }
}
