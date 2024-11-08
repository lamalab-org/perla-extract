from typing import Union
from pathlib import Path
import os
from loguru import logger
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.export import to_json
from perovscribe.llm_factory import LLMFactory


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
        max_tokens (int): Maximum number of tokens in the response.
            Defaults to 4096.
    """

    def __init__(
        self,
        provider_name: str = "anthropic",
        model_name: str = None,
        preprocessor: str = "marker",
        postprocessor: str = "NONE",
        cache_dir: Union[Path, str] = "",
        use_cache: bool = True,
        max_tokens: int = 4096,
    ):
        logger.info(f"Initializing ExtractionPipeline with provider: {provider_name}")
        self.provider = LLMFactory.create_provider(
            provider_name,
            model_name=model_name,
            max_tokens=max_tokens,
        )
        self.preprocessor = Preprocessor(
            preprocessor,
            cache_dir_root=cache_dir,
            use_cache=use_cache,
        )
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.max_tokens = max_tokens
        logger.debug(
            f"ExtractionPipeline initialized successfully with max_tokens: {max_tokens}"
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
        output = output + os.path.split(filepath)[1][:-4] + ".json"
        logger.debug(f"Output will be saved to: {output}")

        pdf_text = self.preprocessor.pdf_to_text(filepath)
        logger.debug("PDF text extraction completed")

        # Use the provider directly to extract data
        results: PerovskiteSolarCells = self.provider.extract_data(pdf_text)
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
    max_tokens: int = 4096,
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
        max_tokens (int): Maximum number of tokens in the response.
            Defaults to 4096.

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
        max_tokens=max_tokens,
    ).run(filepath)


def main_cli():
    """Command-line interface entry point."""
    import fire

    logger.info("Starting CLI interface")
    fire.Fire(extract)
