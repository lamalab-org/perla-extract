from pint import UnitRegistry
from typing import Dict, Any
from loguru import logger
import io
import requests

ureg = UnitRegistry()
Q_ = ureg.Quantity
ureg.default_preferred_units = [
    ureg.V,
    ureg.cm**2,
    ureg.L,
    ureg.degC,
    ureg.s,
    ureg.nm,
    ureg.mbar,
    ureg.eV,
    ureg.mW / ureg.cm**2,
    ureg.mA / ureg.cm**2,
]


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


def convert_units(parent_key: str, obj: Dict[str, Any]) -> Any:
    """
    Convert units of values in a nested dictionary to preferred units.

    Args:
        parent_key (str): Key of the parent dictionary
        obj (Dict[str, Any]): Nested dictionary containing values with units

    Returns:
        Any: float value or Dictionary with concentration values
    """

    if obj["value"] is None:
        return None

    new_dict = {}
    if "unit" not in obj:  # For FF there is no unit
        new_dict = obj["value"]
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
                quantity = Q_(obj["value"], ureg(obj["unit"]))
                if (
                    parent_key == "PCE_T80"
                ):  # For PCE_T80, convert to hours instead of seconds
                    converted = quantity.to_preferred(ureg.hour)
                else:
                    converted = float(quantity.to_preferred().magnitude)
            new_dict = converted
        except Exception as e:
            # Keep original if conversion fails
            logger.error(f"Failed to convert {obj['value']} {obj['unit']} Error:{e}")
            new_dict = None
    return new_dict


def convert_to_nomad_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Traverse a nested dictionary and convert values with units to preferred units.

    Args:
        data (Dict[str, Any]): Nested dictionary containing values with units

    Returns:
        Dict[str, Any]: Dictionary with converted values
    """

    def traverse_and_convert(parent_key: str, obj: Any) -> Any:
        if isinstance(obj, dict):
            # Create a new dictionary to store modified values
            new_dict = {}
            if "value" in obj:
                new_dict = convert_units(parent_key, obj)
            else:
                # Recursively process all key-value pairs
                if parent_key == "cells" and obj["layers"] is not None:
                    new_dict["layer_order"] = get_layer_order(obj["layers"])

                for key, value in obj.items():
                    if (
                        key == "additional_parameters"
                    ):  # For additional parameters, keep JSON structure
                        new_dict[key] = value
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
                    else:
                        new_dict[key] = traverse_and_convert(key, value)
            return new_dict

        elif isinstance(obj, list):
            return [traverse_and_convert(parent_key, item) for item in obj]

        else:
            return obj

    return traverse_and_convert(None, data)


import logging

logger = logging.getLogger(__name__)


def get_authentication_token(nomad_url: str, username: str, password: str) -> str:
    """Get the token for accessing your NOMAD unpublished uploads remotely"""
    try:
        response = requests.get(
            nomad_url + "auth/token",
            params=dict(username=username, password=password),
            timeout=10,
        )
        token = response.json().get("access_token")
        if token:
            return token

        logger.error(f"response is missing token: {response.json()}")
        return
    except Exception:
        logger.error("something went wrong trying to get authentication token")
        return


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


def push_to_nomad(
    doi: str, response, nomad_url: str, token: str, upload_id: str = None
):
    response = convert_to_nomad_schema(response)
    for index, cell in enumerate(response["cells"]):
        transformed_data = {"data": cell}
        transformed_data["data"]["DOI_number"] = (
            f"https://www.doi.org/{doi.replace("--", "/")}"
        )
        transformed_data["data"]["m_def"] = (
            "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell"
        )
        transformed_data = remove_none_values(transformed_data)

        # Convert the transformed data back to JSON format
        transformed_json = json.dumps(transformed_data, indent=4)
        file = io.StringIO(transformed_json)
        if upload_id is None:
            print("comes here")
            res = requests.post(
                f"{nomad_url}uploads",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                files={"file": (doi + "-cell-" + str(index) + ".archive.json", file)},
                timeout=30,
            )
        else:
            res = requests.put(
                f"{nomad_url}uploads/{upload_id}/raw/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                files={"file": (doi + "-cell-" + str(index) + ".archive.json", file)},
                timeout=30,
            )
        upload_id = res.json().get("upload_id")
        if upload_id:
            logger.info(
                f"Doi:{doi} Cell:{index} Upload Id:{upload_id} Status Code:{res.status_code}"
            )
        else:
            logger.error(f"Response is missing upload_id for Doi:{doi} Cell:{index}")
            logger.error(f"Response:{res.json()}")


from glob import glob
import json

URL = "https://nomad-lab.eu/prod/v1/oasis/api/v1/"
PASSWORD = ""
USERNAME = ""

json_file_paths = glob("30_papers/*.json")
token = get_authentication_token(URL, USERNAME, PASSWORD)
print(token)
for i, json_file_path in enumerate(json_file_paths[:]):
    doi = json_file_path.split("/")[1][:-5]
    data = json.load(open(json_file_path, "rb"))
    push_to_nomad(doi, data, URL, token, upload_id=None)
    print(f"Completed upload for {json_file_path}")
    print(i)