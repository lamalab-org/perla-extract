from perovscribe.llm_factory import LLMFactory
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


def create_text_completion(
    provider_name: str,
    model_name: str,
    pdf_text: str,
) -> PerovskiteSolarCells:
    """
    Extract perovskite solar cell data from a PDF using preprocessing and LLM.

    Arguments:
        provider_name (str): name of the LLM provider (e.g., "anthropic")
        model_name (str): name of the specific model to use
        pdf_text (str): the text content from the PDF

    Returns:
        PerovskiteSolarCells: the response from the LLM containing extracted perovskite solar cell data
    """
    provider = LLMFactory.create_provider(provider_name, model_name)
    return provider.extract_data(pdf_text)
