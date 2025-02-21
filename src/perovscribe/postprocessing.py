import warnings

from perovscribe import configuration as config


def postprocess(data: dict) -> dict:
    # data = add_device_stack(data)
    data = normalize(data)
    return data


def normalize_perovskite():
    pass


def convert_to_mg_per_L(value: float, unit_str: str) -> float:
    """
    Convert various concentration units to mg/L

    Args:
        value (float): The numerical value of the concentration
        unit_str (str): The unit string, one of: 'mol/L', 'mmol/L', 'g/L',
                       'mg/L', 'mg/mL', 'wt%', 'vol%', 'M'

    Returns:
        float: The concentration in mg/L

    Raises:
        ValueError: If the unit string is not recognized or conversion is not possible
    """
    # Create mapping for special cases and aliases
    unit_mapping = {
        "M": "mol/L",
        "wt%": "g/100g",  # Approximate conversion assuming density = 1 g/mL
        "vol%": "mL/100mL",
    }

    # Replace aliases with standard forms
    unit_str = unit_mapping.get(unit_str, unit_str)

    try:
        # Handle special cases
        if unit_str in ["g/100g", "mL/100mL"]:
            # Convert percentage to g/L assuming density of 1 g/mL
            value_g_per_L = value * 10  # Convert from % to g/L
            quantity = value_g_per_L * config.ureg("g/L")
        else:
            # Create quantity with original units
            quantity = value * config.ureg(unit_str)

        # Handle molar concentrations
        if str(quantity.units) in ["mol/L", "mmol/L"]:
            # Assuming molecular weight conversion is needed
            # You would need to specify the molecular weight for your specific compound
            molecular_weight = (
                100  # Example: replace with actual molecular weight in g/mol
            )
            if str(quantity.units) == "mol/L":
                quantity = quantity * molecular_weight * 1000  # Convert to mg/L
            else:  # mmol/L
                quantity = quantity * molecular_weight  # Convert to mg/L

        # Convert to mg/L
        result = quantity.to("mg/L")

        return result.magnitude

    except Exception as e:
        raise ValueError(f"Could not convert {value} {unit_str} to mg/L: {str(e)}")


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
                # elif quantity_type == (1 * ureg.mg / ureg.mL).dimensionality:
                #     convert_to_mg_per_L(data["value"], data["unit"])
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
            [layer.get("name", "") for layer in cell.get("layers", [])]
        )
    return data


def get_empty_template():
    """Returns the complete empty template dictionary structure."""
    return {
        "perovskite_composition": {
            "formula": None,
            "dimensionality": None,
            "a_ions": [
                {
                    "abbreviation": None,
                    "common_name": None,
                    "molecular_formula": None,
                    "coefficient": None,
                }
            ],
            "b_ions": [
                {
                    "abbreviation": None,
                    "common_name": None,
                    "molecular_formula": None,
                    "coefficient": None,
                }
            ],
            "x_ions": [
                {
                    "abbreviation": None,
                    "common_name": None,
                    "molecular_formula": None,
                    "coefficient": None,
                }
            ],
            "bandgap": {"value": None, "unit": None},
        },
        "device_architecture": None,
        "pce": {"value": None, "unit": None},
        "jsc": {"value": None, "unit": None},
        "voc": {"value": None, "unit": None},
        "ff": {"value": None},
        "number_devices": None,
        "averaged_quantities": None,
        "active_area": {"value": None, "unit": None},
        "light_source": {
            "type": None,
            "description": None,
            "light_intensity": {"value": None, "unit": None},
            "lamp": None,
        },
        "encapsulated": None,
        "additional_notes": None,
        "stability": {
            "time": {"value": None, "unit": None},
            "light_intensity": {"value": None, "unit": None},
            "humidity": {"value": None, "unit": None},
            "temperature": {"value": None, "unit": None},
            "PCE_T80": {"value": None, "unit": None},
            "PCE_at_the_start_of_the_experiment": {"value": None, "unit": None},
            "PCE_after_1000_hours": {"value": None, "unit": None},
            "PCE_at_the_end_of_description": {"value": None, "unit": None},
            "potential_bias": None,
        },
        "layers": [
            {
                "name": None,
                "thickness": {"value": None, "unit": None},
                "functionality": None,
                "deposition": [
                    {
                        "step_name": None,
                        "method": None,
                        "atmosphere": None,
                        "temperature": {"value": None, "unit": None},
                        "duration": {"value": None, "unit": None},
                        "antisolvent": None,
                        "solution": {
                            "compounds": None,
                            "solutes": [
                                {
                                    "name": None,
                                    "concentration": {"value": None, "unit": None},
                                }
                            ],
                            "volume": {"value": None, "unit": None},
                            "temperature": {"value": None, "unit": None},
                            "solvents": [{"name": None, "volume_fraction": None}],
                        },
                        "additional_parameters": None,
                    }
                ],
                "additional_treatment": None,
            }
        ],
    }


def merge_dicts(template, data, parent_key=""):
    """
    Recursively merges a data dictionary into a template, filling in missing keys with None.
    Handles lists and nested dictionaries.
    """
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
    }

    if isinstance(template, dict) and isinstance(data, dict):
        result = {}
        # First add all keys from the template
        for key in template:
            if key in data:
                if key == "concentration" and "concentration_unit" in data:
                    result[key] = {
                        "value": data[key],
                        "unit": data["concentration_unit"],
                    }
                else:
                    result[key] = merge_dicts(template[key], data[key], key)
            else:
                result[key] = template[key]

        return result
    elif isinstance(template, list) and isinstance(data, list):
        # For lists, we use the first item in the template as a pattern
        if not template:
            return data
        template_item = template[0]
        return [merge_dicts(template_item, item) for item in data]
    else:
        if isinstance(template, dict):
            print(
                data, "=================================", parent_key
            ) if data is not None else print("")
            if data is not None:
                return {"value": data, "unit": default_units[parent_key]}
            return template
        return data


def complete_solar_cell_dict(partial_data):
    """
    Takes a partially filled solar cell dictionary and fills in missing keys with None values.
    """
    template = get_empty_template()
    # TODO Make loop here for all cells so normalize can be ran on this. concentration_unit needs to be handled.
    return merge_dicts(template, partial_data)
