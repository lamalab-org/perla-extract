from typing import Union
from pathlib import Path
from loguru import logger
from typing import Dict, Optional
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.export import to_json
from perovscribe.providers.llm_factory import LLMFactory


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data.

    Args:
        provider_name (str): Name of the LLM provider (e.g., "anthropic", "openai")
        model_name (str, optional): Name of the specific model to use.
            If None, uses the default model for the provider.
        preprocessor (str): Name of the preprocessor to use
        postprocessor (str): Name of the postprocessor to use
        cache_dir (Union[Path, str]): The root directory for the diskcache
        use_cache (bool): True if caching should be utilized
        model_kwargs (Optional[Dict]): Additional keyword arguments for the model
            - max_tokens (int): Maximum number of tokens in the response.
            - temperature (float): Sampling temperature for the model.
            - other model-specific arguments
    """

    def __init__(
        self,
        provider_name: str = "anthropic",
        model_name: str = None,
        preprocessor: str = "marker",
        postprocessor: str = "NONE",
        cache_dir: Union[Path, str] = "",
        use_cache: bool = True,
        model_kwargs: Optional[Dict] = None,
    ):
        logger.info(f"Initializing ExtractionPipeline with provider: {provider_name}")

        # Updated to use new LLMFactory
        self.llm = LLMFactory(
            provider_name=provider_name,
            model_name=model_name,
            model_kwargs=model_kwargs,
        )

        self.preprocessor = Preprocessor(
            preprocessor,
            cache_dir_root=cache_dir,
            use_cache=use_cache,
        )
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.model_kwargs = model_kwargs
        logger.debug(
            f"ExtractionPipeline initialized successfully with model_kwargs: {model_kwargs}"
        )

    def run(
        self,
        filepath: Union[Path, str],
        output: Union[Path, str] = "./",
    ) -> PerovskiteSolarCells:
        """Run the extraction pipeline on a PDF file.

        Args:
            filepath (Union[Path, str]): Path to the PDF file
            output (Union[Path, str]): Path where to save the output JSON.
                Defaults to "./".

        Returns:
            PerovskiteSolarCells: Extracted data in structured format
        """
        logger.info(f"Processing file: {filepath}")
        output = Path(output) / f"{Path(filepath).stem}.json"
        logger.debug(f"Output will be saved to: {output}")

        pdf_text = self.preprocessor.pdf_to_text(filepath)
        logger.debug("PDF text extraction completed")

        # Updated to use new LLMFactory
        results: PerovskiteSolarCells = self.llm.extract_data(pdf_text)
        logger.debug("Data extraction completed")

        # Save results to JSON
        to_json(results, output)
        logger.info(f"Results saved to: {output}")

        return results


def extract(
    filepath: str,
    provider_name: str = "anthropic",
    model_name: str = None,
    preprocessor: str = "marker",
    postprocessor: str = "NONE",
    cache_dir: str = "",
    use_cache: bool = True,
    model_kwargs: Optional[Dict] = None,
) -> PerovskiteSolarCells:
    """Extract data from a PDF file.

    Args:
        filepath (str): Path to the PDF file
        provider_name (str): Name of the LLM provider ("anthropic" or "openai")
        model_name (str, optional): Specific model to use. If None, uses provider's default
        preprocessor (str): Name of the preprocessor to use
        postprocessor (str): Name of the postprocessor to use
        cache_dir (str): Directory for caching
        use_cache (bool): Whether to use caching
        model_kwargs (Optional[Dict]): Additional keyword arguments for the model

    Returns:
        PerovskiteSolarCells: Extracted data in structured format
    """
    logger.info("Starting extraction process")
    return ExtractionPipeline(
        provider_name=provider_name,
        model_name=model_name,
        preprocessor=preprocessor,
        postprocessor=postprocessor,
        cache_dir=cache_dir,
        use_cache=use_cache,
        model_kwargs=model_kwargs,
    ).run(filepath)


def main_cli():
    """Command-line interface entry point."""
    import fire

    logger.info("Starting CLI interface")
    fire.Fire(extract)
