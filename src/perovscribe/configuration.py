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
        ureg.nanometer.dimensionality: (ureg.nanometer, "nm"),  # Thickness
        ureg.day.dimensionality: (
            ureg.second,
            "s",
        ),  # Time (converted to hours for finer granularity)
        ureg.celsius.dimensionality: (
            ureg.celsius,
            "°C",
        ),  # Temperature converted from Celsius
    }
}
