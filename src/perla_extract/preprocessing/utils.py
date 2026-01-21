import hashlib
from typing import Union
from pathlib import Path


def get_hash(filepath: Union[Path, str], mode: str = "md5") -> str:
    h = hashlib.new(mode)
    with open(filepath, "rb") as file:
        data = file.read()
    h.update(data)
    digest = h.hexdigest()
    return digest
