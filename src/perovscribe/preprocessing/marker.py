from marker.convert import convert_single_pdf
from marker.models import load_all_models
from perovscribe.preprocessing.base import BasePreprocessor
from perovscribe.types import PathType
_models = load_all_models()

class MarkerPreprocessor(BasePreprocessor):
    def _pdf_to_text(self, pdf_path: PathType) -> str:
        full_text, images, out_meta = convert_single_pdf(pdf_path,_models)
