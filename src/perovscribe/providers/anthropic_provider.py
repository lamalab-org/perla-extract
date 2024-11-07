import instructor
from anthropic import Anthropic
import logging
from perovscribe.providers.base_provider import LLMProvider
from perovscribe.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Implementation of LLM provider for Anthropic's Claude."""

    def extract_data(self, pdf_text: str) -> PerovskiteSolarCells:
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
            max_tokens=8192,
            messages=messages,
            response_model=PerovskiteSolarCells,
            temperature=0,
        )
        return resp
