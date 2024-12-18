import warnings

from perovscribe import configuration as config


def postprocess(data: dict) -> dict:
    data = add_device_stack(data)
    data = normalize(data)
    return data


def normalize_perovskite():
    pass


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
    default_units_by_type = config.pint["default_units_by_type"]
    for key, value in data.items():
        if isinstance(value, dict):
            # Recursively handle nested dictionaries
            data[key] = normalize(value)
        elif isinstance(value, list):
            # Handle lists by normalizing each element
            data[key] = [
                normalize(item) if isinstance(item, (dict, list, tuple)) else item
                for item in value
            ]
        elif (
            key == "value"
            and "unit" in data
            and data["value"] is not None
            and data["unit"] is not None
        ):
            try:
                quantity = config.ureg.Quantity(value, config.ureg.Unit(data["unit"]))

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
                else:
                    warnings.warn(
                        f"No default unit type found for the following unit during normalization: {data['unit']}"
                    )
            except Exception as e:
                print(f"Error converting {value} {data['unit']}: {e}")

    return data


def add_device_stack(data: dict) -> dict:
    for id, cell in enumerate(data["cells"]):
        data["cells"][id]["device_stack"] = " ".join(
            [layer.get("name", "") for layer in cell.get("layers") or []]
        )
    return data
