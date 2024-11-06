from perovscribe.preprocessing.base import BasePreprocessor
from typing import Union
from pathlib import Path
import pymupdf


class PyMuPDFPreprocessor(BasePreprocessor):
    def _pdf_to_text(self, pdf_path: Union[Path, str]) -> str:
        doc = pymupdf.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n\n"
        return text
