from typing import Optional
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import Union
from pathlib import Path
from perovscribe.preprocessing import Preprocessor


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data."""

    def __init__(
        self,
        model_name: str,
        preprocessor: str,
        postprocessor: str,
        cache_dir: Union[Path, str],
        use_caching: bool = True,
    ):
        """
        Initialize the extraction pipeline.

        Args:
            model_name (str): name of the LLM to call
            cache_dir (Path | str): the root directory for the diskcache
            use_caching (bool): True if caching should be utilized
        """
        self.model_name = model_name
        self.preprocessor = Preprocessor(preprocessor)
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_caching = use_caching

    def extract_from_pdf(
        self, filepath: Union[Path, str]
    ) -> Optional[PerovskiteSolarCells]: ...
