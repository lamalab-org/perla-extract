from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import Union
from pathlib import Path

def to_json(pydantic_model: PerovskiteSolarCells, output: Union[Path, str]):
    with open(output, "w") as f:
        f.write(pydantic_model.model_dump_json())
