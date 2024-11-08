import instructor
from anthropic import Anthropic
from loguru import logger
from perovscribe.providers.base_provider import LLMProvider
from perovscribe.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


class AnthropicProvider(LLMProvider):
    """Implementation of LLM provider for Anthropic's Claude.

    Args:
        model_name (str): Name of the model to use
        max_tokens (int, optional): Maximum number of tokens in the response.
            Defaults to 4096.
    """

    def __init__(self, model_name: str, max_tokens: int = 4096):
        super().__init__(model_name)
        self.max_tokens = max_tokens

    def extract_data(self, pdf_text: str) -> PerovskiteSolarCells:
        """Extract structured data from PDF text using Anthropic's Claude.

        Args:
            pdf_text (str): Text content extracted from PDF

        Returns:
            PerovskiteSolarCells: Structured data extracted from the text
        """
        logger.info(f"Using Anthropic provider with model: {self.model_name}")
        client = instructor.from_anthropic(Anthropic())

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{INSTRUCTION_TEXT}\n\nHere is the text:\n{pdf_text}",
            },
        ]

        resp = client.messages.create(
            model=self.model_name,
            max_tokens=self.max_tokens,
            messages=messages,
            response_model=PerovskiteSolarCells,
            temperature=0,
        )

        logger.debug(f"Successfully extracted data using {self.model_name}")
        return resp
