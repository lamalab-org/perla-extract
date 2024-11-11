from typing import Dict, Optional
from perovscribe.providers.llm_factory import LLMFactory
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


def create_text_completion(
    provider_name: str, model_name: str, pdf_text: str, model_kwargs: Optional[Dict]
) -> PerovskiteSolarCells:
    """Extract perovskite solar cell data from a PDF using preprocessing and LLM.

    Args:
        provider_name (str): Name of the LLM provider
        model_name (str): Name of the specific model to use
        pdf_text (str): Text content from PDF to analyze
        model_kwargs (Optional[Dict]): Additional keyword arguments for the model

    Returns:
        PerovskiteSolarCells: Extracted data in structured format
    """
    if model_kwargs is None:
        model_kwargs = {}
    llm = LLMFactory(provider_name, model_name, **model_kwargs)
    return llm.extract_data(pdf_text)
