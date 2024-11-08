from perovscribe.preprocessing.base import BasePreprocessor
from typing import Union
from pathlib import Path


def get_preprocessor(name, cache_dir_root: Union[Path, str], use_cache: bool):
    if name == "nougat":
        raise NotImplementedError("Nougat preprocessing is not implemented")
    elif name == "pymupdf":
        from perovscribe.preprocessing.pymupdf_processor import PyMuPDFPreprocessor

        return PyMuPDFPreprocessor(
            name, cache_dir_root=cache_dir_root, use_cache=use_cache
        )
    elif name == "marker":
        from perovscribe.preprocessing.marker_processor import MarkerPreprocessor

        return MarkerPreprocessor(
            name, cache_dir_root=cache_dir_root, use_cache=use_cache
        )
    else:
        raise NotImplementedError(f"Preprocessing method {name} is not implemented")


class Preprocessor(BasePreprocessor):
    def __init__(
        self, name: str, cache_dir_root: Union[Path, str], use_cache: bool = True
    ):
        self.preprocessor = get_preprocessor(name, cache_dir_root, use_cache)

    def pdf_to_text(self, pdf_path: Union[Path, str]) -> str:
        return self.preprocessor.pdf_to_text(pdf_path)
