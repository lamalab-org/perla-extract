from loguru import logger
from typing import Optional, Dict
from perovscribe.providers.base_provider import LLMProvider


def get_provider(
    provider_name: str,
    model_name: Optional[str] = None,
    model_kwargs: Optional[Dict] = None,
) -> LLMProvider:
    """Create an instance of the specified LLM provider.

    Args:
        provider_name (str): Name of the LLM provider
        model_name (Optional[str]): Name of the specific model to use
        model_kwargs (Optional[Dict]): Additional keyword arguments for the model

    Returns:
        LLMProvider: Instance of the specified provider

    Raises:
        NotImplementedError: If provider is not supported
    """
    default_models = {
        "anthropic": "claude-3-5-sonnet-20240620",
        "openai": "gpt-4-0424",
    }

    if provider_name == "anthropic":
        from perovscribe.providers.anthropic_provider import AnthropicProvider

        if model_name is None:
            model_name = default_models["anthropic"]
        return AnthropicProvider(model_name=model_name, model_kwargs=model_kwargs)

    elif provider_name == "openai":
        from perovscribe.providers.openai_provider import OpenAIProvider

        if model_name is None:
            model_name = default_models["openai"]
        return OpenAIProvider(model_name=model_name, model_kwargs=model_kwargs)

    else:
        available_providers = ["anthropic", "openai"]
        logger.error(
            f"Provider {provider_name} not supported. Available providers: {available_providers}"
        )
        raise NotImplementedError(
            f"Provider {provider_name} not supported. Available providers: {available_providers}"
        )


class LLMFactory:
    """Factory class for creating LLM provider instances.

    Args:
        provider_name (str): Name of the LLM provider
        model_name (Optional[str]): Name of the specific model to use
        model_kwargs (Optional[Dict]): Additional keyword arguments for the model
    """

    def __init__(
        self,
        provider_name: str,
        model_name: Optional[str] = None,
        model_kwargs: Optional[Dict] = None,
    ):
        self.provider = get_provider(
            provider_name,
            model_name=model_name,
            model_kwargs=model_kwargs,
        )

    def extract_data(self, pdf_text: str):
        """Extract data using the configured provider.

        Args:
            pdf_text (str): Text content from PDF to analyze

        Returns:
            PerovskiteSolarCells: Extracted data in structured format
        """
        return self.provider.extract_data(pdf_text)
