from perovscribe.preprocessing.base import BasePreprocessor
from perovscribe.types import PathType
import pymupdf

class MarkerPreprocessor(BasePreprocessor):
    def _pdf_to_text(self, pdf_path: PathType) -> str:
        doc = pymupdf.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n\n"
        return text
