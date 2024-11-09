from perovscribe.providers.llm_factory import LLMFactory
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


def create_text_completion(
    provider_name: str,
    model_name: str,
    pdf_text: str,
) -> PerovskiteSolarCells:
    """Extract perovskite solar cell data from a PDF using preprocessing and LLM.

    Args:
        provider_name (str): Name of the LLM provider
        model_name (str): Name of the specific model to use
        pdf_text (str): Text content from PDF to analyze

    Returns:
        PerovskiteSolarCells: Extracted data in structured format
    """
    llm = LLMFactory(provider_name, model_name)
    return llm.extract_data(pdf_text)
