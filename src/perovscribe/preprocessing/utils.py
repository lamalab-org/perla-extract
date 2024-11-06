import hashlib
from perovscribe.types import PathType


def get_hash(filepath: PathType, mode: str = "md5") -> str:
    h = hashlib.new(mode)
    with open(filepath, "rb") as file:
        data = file.read()
    h.update(data)
    digest = h.hexdigest()
    return digest
