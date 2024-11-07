from abc import ABC, abstractmethod
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, model_name: str):
        self.model_name = model_name

    @abstractmethod
    def extract_data(self, pdf_text: str) -> PerovskiteSolarCells:
        """Extract data from text using the LLM provider."""
        pass
