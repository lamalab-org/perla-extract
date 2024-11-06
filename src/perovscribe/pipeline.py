from typing import Optional
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import Union
from pathlib import Path
from perovscribe.preprocessing.preprocessor import Preprocessor


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data."""

    def __init__(
        self,
        model_name: str,
        preprocessor: str,
        postprocessor: str,
        cache_dir: Union[Path, str],
        use_cache: bool = True,
    ):
        """
        Initialize the extraction pipeline.

        Args:
            model_name (str): name of the LLM to call
            cache_dir (Path | str): the root directory for the diskcache
            use_cache (bool): True if caching should be utilized
        """
        self.model_name = model_name
        self.preprocessor = Preprocessor(preprocessor, cache_dir_root=cache_dir, use_cache=use_cache)
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_cache = use_cache

    def extract_from_pdf(
        self, filepath: Union[Path, str]
    ) -> Optional[PerovskiteSolarCells]: ...

    def run(self, filepath: Union[Path, str], output: Union[Path, str] = "./"):
        pdftext = self.preprocessor.pdf_to_text(filepath)
        # TODO: Call LLM_call, postprocessor, export 

def extract(filepath: str, model_name: str = "claude-3-5-sonnet-20240620", preprocessor: str = "marker", postprocessor: str = "NONE", cache_dir: str ="", use_cache: bool = True):
    ExtractionPipeline(model_name, preprocessor, postprocessor, cache_dir, use_cache).run(filepath)

def main_cli():
    import fire
    fire.Fire(extract)
