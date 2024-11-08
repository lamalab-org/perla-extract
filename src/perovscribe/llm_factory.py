from typing import Dict, Type
from loguru import logger
from perovscribe.providers import LLMProvider, AnthropicProvider, OpenAIProvider


class LLMFactory:
    """Factory class for creating LLM provider instances."""

    _providers: Dict[str, Type[LLMProvider]] = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
    }

    # Default models for each provider
    _default_models: Dict[str, str] = {
        "anthropic": "claude-3-5-sonnet-20240620",
        "openai": "gpt-4o-2024-05-13",
    }

    @classmethod
    def create_provider(
        cls,
        provider_name: str,
        model_name: str = None,
        max_tokens: int = 4096,
    ) -> LLMProvider:
        """Create an instance of the specified LLM provider.

        Args:
            provider_name (str): Name of the provider (e.g., "anthropic", "openai")
            model_name (str, optional): Name of the specific model to use.
                If None, uses the default model for the provider.
            max_tokens (int): Maximum number of tokens in the response.
                Defaults to 4096.

        Returns:
            LLMProvider: Instance of the specified provider

        Raises:
            ValueError: If the provider is not supported
        """
        if provider_name not in cls._providers:
            available_providers = list(cls._providers.keys())
            logger.error(
                f"Provider {provider_name} not supported. Available providers: {available_providers}"
            )
            raise ValueError(
                f"Provider {provider_name} not supported. Available providers: {available_providers}"
            )

        # Use default model if none specified
        if model_name is None:
            model_name = cls._default_models[provider_name]
            logger.debug(f"Using default model for {provider_name}: {model_name}")

        logger.info(
            f"Creating provider: {provider_name} with model: {model_name} and max_tokens: {max_tokens}"
        )
        provider_class = cls._providers[provider_name]
        return provider_class(model_name=model_name, max_tokens=max_tokens)
