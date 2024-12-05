from pint import UnitRegistry

# Initialize the UnitRegistry
ureg = UnitRegistry()


def normalize(data: dict) -> dict:
    """
    Recursively walks through a dictionary and converts 'value' and 'unit' pairs
    to default units based on the quantity type.

    Parameters:
        data (dict): The input dictionary.

    Returns:
        dict: The updated dictionary with converted values.
    """
    # Define default units for each quantity type
    default_units_by_type = {
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
    for key, value in data.items():
        if isinstance(value, dict):
            # Recursively handle nested dictionaries
            data[key] = normalize(value)
        elif isinstance(value, list):
            # Handle lists by normalizing each element
            data[key] = [
                normalize(item) if isinstance(item, (dict, list)) else item
                for item in value
            ]
        elif key == "value" and "unit" in data:
            try:
                quantity = ureg.Quantity(value, ureg.Unit(data["unit"]))

                # Determine the quantity type (e.g., length, speed)
                quantity_type = quantity.dimensionality
                if quantity_type in default_units_by_type:
                    default_unit, default_unit_str = default_units_by_type[
                        quantity_type
                    ]
                    # Convert to the default unit
                    converted_quantity = quantity.to(default_unit)
                    # Update the dictionary
                    data["value"] = round(converted_quantity.magnitude, 2)
                    data["unit"] = default_unit_str
            except Exception as e:
                print(f"Error converting {value} {data['unit']}: {e}")

    return data
