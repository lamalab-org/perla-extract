from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import TYPE_CHECKING, Union
from pathlib import Path
from typing import Dict, Any
import math

if TYPE_CHECKING:
    from pint import UnitRegistry

def to_json(pydantic_model: PerovskiteSolarCells, output: Union[Path, str]):
    with open(output, "w") as f:
        f.write(pydantic_model.model_dump_json())


def convert_units(parent_key: str, obj: Dict[str, Any], ureg: 'UnitRegistry') -> Any:
    """
    Convert units of values in a nested dictionary to preferred units.

    Args:
        parent_key (str): Key of the parent dictionary
        obj (Dict[str, Any]): Nested dictionary containing values with units
        ureg (UnitRegistry): Pint UnitRegistry for unit conversion

    Returns:
        Any: float value or Dictionary with concentration values
    """

    if obj["value"] is None:
        return None

    if "unit" not in obj:  # For FF there is no unit
        converted = obj["value"]
    else:
        try:
            if obj["unit"] == "%":  # For % values no conversion is needed
                converted = obj["value"]
            elif (
                parent_key == "concentration"
            ):  # For concentration values, units need to preserved
                converted = {}
                converted["concentration"] = obj["value"]
                converted["concentration_unit"] = obj["unit"]
            else:
                converted = ureg.Quantity(obj["value"], obj["unit"])
        except Exception as e:
            # Keep original if conversion fails
            print(f"Failed to convert {obj['value']} {obj['unit']} Error:{e}")
            converted = obj["value"]
    return converted


def get_layer_order(layers: Dict[str, Any]) -> str:
    """
    Get the order of layers in a cell stack.
    Args:
        layers (Dict[str, Any]): List of layers in a cell

    Returns:
        str: Comma-separated string of layer names
    """
    layer_order = ""
    for layer in layers:
        layer_order += f"{layer['name']}," if layer["name"] is not None else ""
    return layer_order[:-1]


def convert_to_nomad_schema(data: Dict[str, Any], ureg: 'UnitRegistry') -> Dict[str, Any]:
    """
    Traverse a nested dictionary and convert values with units to preferred units.

    Args:
        data (Dict[str, Any]): Nested dictionary containing values with units
        ureg (UnitRegistry): Pint UnitRegistry for unit conversion

    Returns:
        Dict[str, Any]: Dictionary with converted values
    """

    def traverse_and_convert(parent_key: str, obj: Any) -> Any:
        if isinstance(obj, dict):
            # Create a new dictionary to store modified values
            new_dict = {}
            if "value" in obj:
                new_dict = convert_units(parent_key, obj, ureg)
            else:
                # Recursively process all key-value pairs
                if parent_key == "cells" and obj["layers"] is not None:
                    new_dict["layer_order"] = get_layer_order(obj["layers"])

                for key, value in obj.items():
                    if (
                        key == "additional_parameters"
                    ):  # For additional parameters, keep JSON structure
                        new_dict[key] = value
                    elif key in ["a_ions", "b_ions", "x_ions"]:
                        new_dict["ions_" + key[0] + "_site"] = traverse_and_convert(
                            "ions_" + key[0] + "_site", value
                        )
                    elif key == "concentration":
                        concentration = traverse_and_convert(key, value)
                        if (
                            concentration is not None
                        ):  # For concentration values, units need to preserved
                            new_dict["concentration"] = concentration["concentration"]
                            new_dict["concentration_unit"] = concentration[
                                "concentration_unit"
                            ]
                        else:
                            new_dict["concentration"] = None
                            new_dict["concentration_unit"] = None
                    elif key in ("impurities", "additives"):
                        converted = traverse_and_convert(key, value)
                        new_dict[key] = [converted] if converted is not None else None
                    else:
                        if key == "bandgap":
                            key = "band_gap"
                        elif key == "PCE_at_the_start_of_the_experiment":
                            key = "PCE_at_start"
                        elif key == "PCE_at_the_end_of_experiment":
                            key = "PCE_at_end"
                        new_dict[key] = traverse_and_convert(key, value)

            return new_dict

        elif isinstance(obj, list):
            return [traverse_and_convert(parent_key, item) for item in obj]

        else:
            return obj

    return traverse_and_convert(None, data)


def remove_none_values(input_dict):
    """Recursively remove all None values from a dictionary, including nested dictionaries."""
    if not isinstance(input_dict, dict):
        return (
            input_dict  # Base case: If it's not a dictionary, return the value as is.
        )

    # Recursively process the dictionary and remove None values
    return {
        key: remove_none_values(value)
        for key, value in input_dict.items()
        if value is not None
    }


def filter_unwanted(data: dict) -> dict:
    new_data = {"cells": []}
    for i, cell in enumerate(data["cells"] or []):
        if (
            ((cell.get("pce") or {"value": 28}).get("value") or 28) < 27.5
            and ((cell.get("voc") or {"value": 1}).get("value") or 1)
            < 1.56  # Voltage > 1.56 are tandems. Voltage<4 cuz above 4 are modules
        ):
            if (
                (cell.get("pce", {"value": 0}) or {"value": 0}).get("value", 0) == 0
                or (cell.get("jsc", {"value": 0}) or {"value": 0}).get("value", 0) == 0
                or (cell.get("voc", {"value": 0}) or {"value": 0}).get("value", 0) == 0
                or (cell.get("ff", {"value": 0}) or {"value": 0}).get("value", 0) == 0
            ):
                new_data["cells"].append(data["cells"][i])
                continue
            if math.isclose(
                ((cell.get("pce") or {"value": 99}).get("value") or 99),
                (
                    ((cell.get("jsc") or {"value": 0}).get("value") or 0)
                    * ((cell.get("voc") or {"value": 0}).get("value") or 0)
                    * ((cell.get("ff") or {"value": 0}).get("value") or 0)
                )
                / 100,
                abs_tol=0.2,
            ):
                new_data["cells"].append(data["cells"][i])
                continue
    return new_data


def convert_to_extraction_to_nomad_entries(
    pydantic_model: PerovskiteSolarCells, doi: str, nomad_schema, ureg: 'UnitRegistry'
):
    data = filter_unwanted(pydantic_model.model_dump())
    data = convert_to_nomad_schema(data, ureg)
    nomad_entries = []
    for index, cell in enumerate(data["cells"]):
        transformed_data = cell
        transformed_data["DOI_number"] = (
            f"https://www.doi.org/{doi.replace('--', '/')}"
        )
        transformed_data["m_def"] = (
            "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell"
        )
        transformed_data = remove_none_values(transformed_data)
        nomad_data_section = nomad_schema.m_from_dict(transformed_data)
        entry_dict = {"data": nomad_data_section.m_to_dict(with_root_def=True)}
        nomad_entries.append(entry_dict)
    return nomad_entries
