import requests
import json
import io
from typing import Union, Dict, Any
from pathlib import Path
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from postprocessing import convert_units_in_dict


def to_json(pydantic_model: PerovskiteSolarCells, output: Union[Path, str]):
    with open(output, "w") as f:
        f.write(pydantic_model.model_dump_json())

        
def get_authentication_token(nomad_url: str, username: str, password: str) -> str: 
    '''Get the token for accessing your NOMAD unpublished uploads remotely'''
    try:
        response = requests.get(
            nomad_url + 'auth/token', params=dict(username=username, password=password), timeout=10)
        token = response.json().get('access_token')
        if token:
            return token

        print('response is missing token: ')
        print(response.json())
        return
    except Exception:
        print('something went wrong trying to get authentication token')
        return

def remove_none_values(input_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively remove all None values from a dictionary, including nested dictionaries."""
    if not isinstance(input_dict, dict):
        return input_dict  # Base case: If it's not a dictionary, return the value as is.

    # Recursively process the dictionary and remove None values
    return {
        key: remove_none_values(value) 
        for key, value in input_dict.items() 
        if value is not None
    }

def push_to_nomad(doi: str, response: Dict[str, Any], nomad_url: str, token: str, upload_id: str = None):
    response = convert_units_in_dict(response)
    for index, cell in enumerate(response["cells"]):
        transformed_data = {
            "data": cell}
        transformed_data["data"]["DOI_number"] = doi.replace("--", "/")
        transformed_data["data"]["m_def"] = "perovskite_solar_cell_database.llm_extraction_schema.LLMExtractedPerovskiteSolarCell"        
        transformed_data = remove_none_values(transformed_data)
        
        # Convert the transformed data back to JSON format
        transformed_json = json.dumps(transformed_data, indent=4)
        file = io.StringIO(transformed_json)
        if upload_id is None:
            res = requests.post(f"{nomad_url}uploads/", headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                 files={'file': (doi+"-cell-"+str(index)+".archive.json", file)}, timeout=30)
        else:
            res = requests.put(f"{nomad_url}uploads/{upload_id}/raw/", headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                 files={'file': (doi+"-cell-"+str(index)+".archive.json", file)}, timeout=30)
        upload_id = res.json().get('upload_id')
        if upload_id:
            print(f"doi:{doi} cell:{index}")
            print(upload_id)
            print("pushed!", res.status_code)
        else:
            print(f'response is missing upload_id for doi:{doi} cell:{index}')
            print(res.json())