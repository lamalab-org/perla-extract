from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import TYPE_CHECKING, Union
from pathlib import Path
from typing import Dict, Any
import math
import os
import requests
from loguru import logger
import json
import io

if TYPE_CHECKING:
    from pint import UnitRegistry

NOMAD_USERNAME = os.environ.get("NOMAD_USERNAME")
NOMAD_PASSWORD = os.environ.get("NOMAD_PASSWORD")
NOMAD_URL= os.environ.get('NOMAD_URL', "https://nomad-lab.eu/prod/v1/")

# Mappings: "Old Key" -> "New Key"
KEY_MAPPING = {
    "bandgap": "band_gap",
    "PCE_at_the_start_of_the_experiment": "PCE_at_start",
    "PCE_at_the_end_of_experiment": "PCE_at_end",
    "a_ions": "ions_a_site",
    "b_ions": "ions_b_site",
    "x_ions": "ions_x_site",
    "time": "time", # Keep name, but used for unit check context
}

# Unit Preferences: Define target units for specific dimensions or keys
# If not listed, it defaults to the magnitude of the original value.
UNIT_CONVERSIONS = {
    # Context Key : Target Unit
    "stability": "hour",  # Convert stability time to hours
    "PCE_T80": "hour",
}

# Keys that should be handled specially (not flattened to single float)
PRESERVE_STRUCTURE = ["additional_parameters"]

# Keys that should be split into value and unit fields
SPLIT_VALUE_UNIT = ["concentration"]

# ========== TEMPORARY: SKIP ADDITIVES (DELETE THIS SECTION LATER) ==========
SKIP_KEYS = ["additives"]  # Keys to skip during processing
# ============================================================================

def to_json(pydantic_model: PerovskiteSolarCells, output: Union[Path, str]):
    with open(output, "w") as f:
        f.write(pydantic_model.model_dump_json())


def get_layer_order(layers):
    """Extracts non-null layer names into a comma-separated string."""
    if not layers or not isinstance(layers, list):
        return None
    # Filter layers that have a name and join them
    names = [l["name"] for l in layers if l.get("name")]
    return ",".join(names)


def convert_with_pint(value_dict, parent_key, ureg=None):
    """Converts a {value, unit} dict to a float/int in preferred units."""
    if ureg is None:
        from pint import UnitRegistry
        ureg = UnitRegistry()

    val = value_dict.get("value")
    unit = value_dict.get("unit")

    if val is None:
        return None
    
    # If no unit exists or it's just a number
    if not unit:
        return val

    try:
        # 1. Handle Percentages (return as pure number, e.g. 18.24)
        if unit == "%":
            return val
        
        # 2. Create Quantity
        q = ureg.Quantity(val, unit)
        
        # 3. Check if we have a specific target unit for this parent key
        # (e.g., convert 'stability' time to hours)
        if parent_key in UNIT_CONVERSIONS:
            target_unit = UNIT_CONVERSIONS[parent_key]
            # Check if dimensions match before converting (e.g. time -> time)
            if q.check(target_unit): 
                return float(q.to(target_unit).magnitude)
        
        # 4. Special case: If the specific key is 'time' inside 'stability' dict
        # The recursive parent_key passing makes this slightly tricky, 
        # so we handle "context" logic in the main loop or defaults here.
        # For this specific dataset, stability time is usually hours.
        if unit == 's' and (val > 10000): # Heuristic or explicit rule
             return float(q.to('hour').magnitude)

        # Default: Return magnitude of the original unit provided
        return float(q.magnitude)

    except Exception as e:
        # Fallback: return original value if conversion fails
        return val


def traverse_and_transform(obj, parent_key=None, ureg=None):
    """
    Recursively traverses the data structure to:
    1. Remove None values
    2. Convert {value, unit} objects
    3. Rename keys
    4. Generate derived fields (layer_order)
    5. Split concentration into value and unit fields
    """
    
    # CASE 1: List -> Process items
    if isinstance(obj, list):
        # Process list, filter out None results
        processed_list = [traverse_and_transform(item, parent_key) for item in obj]
        return [i for i in processed_list if i is not None]

    # CASE 2: Dictionary
    if isinstance(obj, dict):
        
        # A. Check for "Value/Unit" Leaf Node
        # Special handling for concentration: split into two fields
        if "value" in obj and parent_key in SPLIT_VALUE_UNIT:
            return {
                parent_key: obj.get("value"),
                f"{parent_key}_unit": obj.get("unit")
            }
        
        # Standard value/unit conversion (flatten to single value)
        if "value" in obj and parent_key not in PRESERVE_STRUCTURE:
            return convert_with_pint(obj, parent_key, ureg)

        # B. Standard Dictionary Processing
        new_dict = {}

        # Hook: Generate layer_order if we see a 'layers' key
        if "layers" in obj and obj["layers"]:
            order = get_layer_order(obj["layers"])
            if order:
                new_dict["layer_order"] = order

        for key, value in obj.items():
            # ========== TEMPORARY: SKIP ADDITIVES (DELETE THIS CHECK LATER) ==========
            if key in SKIP_KEYS:
                continue
            # ==========================================================================
            
            # 1. Skip null values immediately (Cleaning)
            if value is None:
                continue
                
            # 2. Rename Key
            new_key = KEY_MAPPING.get(key, key)
            
            # 3. Recursive Call
            transformed_value = traverse_and_transform(value, key)
            
            # 4. Add to new dict if result is valid
            if transformed_value is not None:
                # Special handling for empty lists/dicts if you want to remove them:
                if isinstance(transformed_value, (dict, list)) and not transformed_value:
                    continue
                
                # If the transformed value is a dict with split fields (like concentration),
                # merge them into the parent dict
                if isinstance(transformed_value, dict) and key in SPLIT_VALUE_UNIT:
                    new_dict.update(transformed_value)
                else:
                    new_dict[new_key] = transformed_value
                
        # If the resulting dict is empty (and it wasn't empty before), return None
        if not new_dict and obj:
            return None
            
        return new_dict

    # CASE 3: Primitives (str, int, float, bool)
    return obj


def process_to_nomad(raw_data, doi, ureg=None):
    """Wraps the transformed data in the specific NOMAD schema envelope."""
    doi_url = "https://www.doi.org/"+doi
    # Run the transformation
    transformed = traverse_and_transform(raw_data, ureg)
    
    output_entries = []
    
    # The input is usually a dict with "cells": [...]
    if "cells" in transformed:
        for cell in transformed["cells"]:
            entry = {
                "data": {
                    "m_def": "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell",
                    "DOI_number": doi_url,
                    **cell # Unpack the processed cell data here
                }
            }
            output_entries.append(entry)
            
    return output_entries

def get_authentication_token(nomad_url: str=NOMAD_URL, username: str=NOMAD_USERNAME, password: str=NOMAD_PASSWORD) -> str|None: 
    '''Get the token for accessing your NOMAD unpublished uploads remotely'''
    body={"username": username, "password": password}
    try:
        response = requests.post(
            nomad_url + 'auth/token', data=body, timeout=10)
        token = response.json().get('access_token')
        if token:
            return token

        logger.error('response is missing token: ')
        logger.error(response.json())
        return None
    except Exception:
        logger.error('something went wrong trying to get authentication token')
        return None


def push_to_nomad(doi: str, response: Dict[str, Any], token: str, upload_id:str|None = None):
    """Push extraction to Nomad"""
    doi=doi.replace('/', '--')
    if len(response)==0:
        logger.warning(f'No extracted cells for Doi:{doi}, skipping upload to NOMAD')
        return
    for index, cell in enumerate(response):
        transformed_json = json.dumps(cell, indent=4)
        file = io.StringIO(transformed_json)
        file_name = doi+"-cell-"+str(index)+".archive.json"
        if upload_id is None:
            res = requests.post(f"{NOMAD_URL}uploads/", headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},params={'wait_for_processing': 'true'},
                 files={'file': file}, timeout=30)
        else:
            res = requests.put(f"{NOMAD_URL}uploads/{upload_id}/raw/", headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},params={'wait_for_processing': 'true'},
                 files={'file': (file_name, file)}, timeout=30)
        upload_id = res.json().get('upload_id')
        if upload_id:
            logger.info(f"Doi:{doi} Cell:{index} Upload Id:{upload_id} Status Code:{res.status_code}")
        else:
            logger.error(f'Response is missing upload_id for Doi:{doi} Cell:{index}')
            logger.error(f"Response:{res.json()}")
            raise Exception('Upload failed, missing upload_id in response')


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
        # Theory filter
        def is_theory_cell(a_cell):
            theory_keywords = [
                r'\bDFT\b', r'\bSCAPS\b', r'\bSCAPS-1D\b', r'density functional', r'first.?principles', 
                r'ab.?initio', r'molecular dynamics', r'\bMD\b simulation', r'VASP', r'Gaussian', 
                r'Quantum ESPRESSO', r'CASTEP', r'SIESTA', r'computational study', r'theoretical study', 
                r'theoretical investigation', r'numerical simulation', r'device simulation', 
                r'theoretical analysis', r'computational analysis', r'theoretical modeling', 
                r'computational modeling', r'simulated', r'simulation of', r'wxAMPS', r'AMPS-1D', 
                r'PC1D', r'AFORS-HET', r'theoretical optimization', r'computational optimization',
                r'simulated performance', r'theoretical efficiency', r'predicted efficiency',
            ]
            pattern = re.compile('|'.join(theory_keywords), re.IGNORECASE)
            notes = a_cell.get('additional_notes', '')
            rev_notes = a_cell.get('reviewer_additional_notes', '')

            text_to_check = f"""
                Additional notes: {notes},
                Additional notes from reviewer: {rev_notes}
                """
            return pattern.search(text_to_check)
        if is_theory_cell(cell):
            continue

        # PCE metrics filter
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


def convert_extraction_to_nomad_entries(
    pydantic_model: PerovskiteSolarCells, doi: str, ureg: 'UnitRegistry' = None
):
    data = filter_unwanted(pydantic_model.model_dump())
    nomad_entries = process_to_nomad(data, doi, ureg)
    return nomad_entries