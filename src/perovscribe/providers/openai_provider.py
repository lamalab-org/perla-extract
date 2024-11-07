import instructor
from openai import OpenAI
import logging
from perovscribe.providers.base_provider import LLMProvider
from perovscribe.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Implementation of LLM provider for OpenAI's GPT models."""

    def extract_data(self, pdf_text: str) -> PerovskiteSolarCells:
        logger.info(f"Using OpenAI provider with model: {self.model_name}")
        client = instructor.patch(OpenAI())

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{INSTRUCTION_TEXT}\n\nHere is the text:\n{pdf_text}",
            },
        ]

        resp = client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            response_model=PerovskiteSolarCells,
            max_tokens=4096,
            temperature=0,
        )
        return resp
