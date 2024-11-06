from pathlib import Path
from typing import Optional
from perovscribe.llm_call import create_text_completion
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.types import PathType


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data."""

    def __init__(
        self,
        model_name: str,
        preprocessor: str,
        postprocessor: str,
        cache_dir: PathType,
        use_caching: bool = True,
    ):
        """
        Initialize the extraction pipeline.

        Args:
            model_name (str): name of the LLM to call
            cache_dir (PathType): the root directory for the diskcache
            use_caching (bool): True if caching should be utilized
        """
        self.model_name = model_name
        self.preprocessor = ...  # call preprocessing factory to obtain preprocessor
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_caching = use_caching

    def extract_from_pdf(
        self, filepath: PathType
    ) -> Optional[PerovskiteSolarCells]: ...
