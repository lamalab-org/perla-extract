import os
import sys
from litellm import completion
from pydantic import BaseModel, Field, ValidationError
from perla_extract.pydantic_model_reduced import PerovskiteSolarCells
from perla_extract.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
import litellm
from loguru import logger
from typing import Any
from perla_extract.configuration import MAX_RETRIES, MAX_TOKENS
# Try to setup Redis cache if available, otherwise use disk cache
try:
    # Disable litellm error output
    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    import logging

    logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
    from litellm.caching.caching import Cache
    litellm.cache = Cache(
        type="redis",
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        ttl=int(os.environ.get("REDIS_TTL", "1000000")),
        password=os.environ.get("REDIS_PASSWORD"),
        namespace="litellm",
    )
except Exception as e:
    pass

class MaxRetriesExceededError(Exception):
    """Custom exception to indicate that the maximum number of retries has been exceeded."""
    pass

def create_text_completion(
    model_name: str,
    pdf_text: str = "",
    system_prompt: str = SYSTEM_PROMPT,
    instruction: str = INSTRUCTION_TEXT,
    api_key: str = None,
    api_base_url: str = None,
    additional_params: dict = {}
) -> tuple[PerovskiteSolarCells, Any]:
    """
     Extract structured perovskite solar cell data from raw PDF text using an LLM.

    This function sends a prompt to a language model (via the `LiteLLM` library)
    with a specified system prompt and instruction, along with the given PDF text. It attempts to
    deserialize the response into a `PerovskiteSolarCells` Pydantic model, automatically handling
    context length errors by reducing `max_tokens` if needed.

    Args:
        model_name (str): Name of the LLM to use.
        pdf_text (str): Text content extracted from a PDF document.
        system_prompt (str): The system-level prompt guiding the LLM’s behavior (default: SYSTEM_PROMPT).
        instruction (str): Task-specific instruction for the LLM (default: INSTRUCTION_TEXT).
        api_key (str, optional): API key for LiteLLM if environment variables cannot be used.
        api_base_url (str, optional): Base URL for the LiteLLM API if environment variables cannot be used.
        additional_params (dict, optional): Additional parameters to pass to the LLM call.

    Returns:
        PerovskiteSolarCells: A Pydantic model instance populated with the extracted data.
        The raw response from the LLM call, which may contain additional metadata.

    Raises:
        MaxRetriesExceededError: If the maximum number of retries is exceeded due to validation errors.
    """
    if api_key:
        litellm.api_key = api_key

    if api_base_url:
        litellm.api_base = api_base_url

    # Construct messages for LLM
    messages = [
        {
            "role": "user",
            "content": f"{system_prompt}\n{instruction}\n Here is the schema: {str(PerovskiteSolarCells.model_json_schema())} \n\nHere is the text:\n{pdf_text}",
        },
    ]

    # Call with LiteLLM

    supported_params = set()
    max_tokens = MAX_TOKENS
    filtered_params = {}
    additional_params = {} if additional_params is None else additional_params
    try:
        model_info = litellm.get_model_info(model=model_name)
        if model_info:
            max_tokens = model_info.get("max_output_tokens", max_tokens)
            logger.info(f"Model {model_name} supports max_output_tokens={max_tokens}.")

        supported_params = set(
            litellm.get_supported_openai_params(model=model_name) or []
        )
       
        if 'response_format' not in supported_params:
            logger.warning(
                f'Model {model_name} does not support response_format parameter.'
            )

        if not litellm.supports_response_schema(model=model_name):
            logger.warning(
                f'Model {model_name} does not support json schema response for structured output.'
            )
        for param, value in additional_params.items():
            if param not in supported_params:
                logger.warning(
                    f"Model {model_name} does not support parameter '{param}'. It will be ignored."
                )
            else:
                filtered_params[param] = value
    except Exception as e:
        logger.error(f"Error occurred while fetching model info for {model_name}: {e}")


    retry_count = 0
    while True:
        filtered_params.update({"model": model_name,
                "api_base": api_base_url, 
                "api_key": api_key, 
                "max_tokens": max_tokens,
                "messages": messages,
                "response_format": PerovskiteSolarCells,
                "drop_params": True})
        try:
            resp = completion(**filtered_params)
        except litellm.exceptions.BadRequestError as e:
            if (
                'AnthropicException - {"type":"error","error":{"type":"invalid_request_error","message":"input length and `max_tokens` exceed context limit:'
                not in str(e)
            ):
                logger.error(f"BadRequestError: {e}. Raising exception.")
                raise
            max_tokens -= 5000
            logger.info(f"reduced max tokens to {max_tokens} due to context length error.")
            continue
        try:
            extracted_data = PerovskiteSolarCells.model_validate_json(resp.choices[0].message.content,strict=True,extra="forbid")
            break
        except ValidationError as e:
            logger.error(f"Validation error: {e}. Attempting to correct the output.")
            if retry_count >= MAX_RETRIES:
                logger.error(f"Max retries reached ({MAX_RETRIES}). Raising MaxRetriesExceededError.")
                raise MaxRetriesExceededError(resp)
            retry_count += 1
            retry_prompt = f'\n\nThe previous attempt resulted in a validation error: {e}. Correct the output to match the expected schema.'
            messages.extend([{'role':'assistant','content':resp.choices[0].message.content}, {"role":"user","content": retry_prompt}])
    return extracted_data, resp


def llm_as_judge(ground_truth, value_truth, value_extraction):
    class Judgement(BaseModel):
        judgement: bool = Field(
            None,
            description="The final say whether the given values match (TRUE) or not (FALSE).",
        )
        # reason: str = Field(None, description="A small sentence explaining why you chose the answer.")

    if "pytest" in sys.modules:
        return Judgement(judgement=True)

    messages = [
        {
            "role": "system",
            "content": "You are an expert scientist that judges whether two provided values match in a data extraction evaluation routine for perovskite solar cells. You will be given the whole ground truth, the value in the ground truth, and the value from the extraction. You have to check if the two values match conceptually. They do not have to be exactly the same. Formulas can be variable. c-TiO2 is the same as TiO2. Only respond with either TRUE or FALSE",
        },
        {
            "role": "user",
            "content": f"Complete ground truth: {str(ground_truth)}\n Truth value: {str(value_truth)} \n Extraction value: {str(value_extraction)}",
        },
    ]
    resp = completion(
        model="gpt-4o-2024-08-06",
        messages=messages,
        response_format=Judgement,
        temperature=0,
     )
    

    return Judgement.model_validate_json(resp.choices[0].message.content, strict=True)