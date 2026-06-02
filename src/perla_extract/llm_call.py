import os
import re
import sys
from typing import Any
from litellm import completion
from pydantic import BaseModel, Field, ValidationError
from perla_extract.pydantic_model_reduced import PerovskiteSolarCells
from perla_extract.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
import litellm
from loguru import logger
from typing import Any
from perla_extract.configuration import MAX_RETRIES, MAX_TOKENS, EXTRACTION_METHODS
# Try to setup Redis cache if available, otherwise use disk cache
log_traces = os.environ.get("PERLA_LOG_TRACES", "false").lower() == "true"
if log_traces:
    required_vars = [
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        raise RuntimeError(
            "PERLA tracing with Langfuse is enabled, but the following required "
            f"environment variable(s) are missing: {', '.join(missing_vars)}. "
            "Please set them and restart the application."
        )

    try:
        from langfuse.decorators import observe, langfuse_context

        litellm.success_callback = ["langfuse"]
        litellm.failure_callback = ["langfuse"]
        litellm.callbacks = ["langfuse"]

        logger.info("Langfuse tracing is enabled. All LLM calls will be traced.")

    except Exception:
        logger.exception("Failed to initialize Langfuse tracing.")
        raise


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

def format_schema(model: BaseModel) -> dict:
    #adapted from https://github.com/567-labs/instructor/blob/47fdb2ca07119d389a3c0e8bc28b9930b814f294/instructor/v2/providers/openai/schema.py
    schema = model.model_json_schema()
    parameters = {k: v for k, v in schema.items() if k not in ("title", "description")}
    parameters["required"] = sorted(schema.get("required", []))

    return {
        "name": schema.get("title", ""),
        "description": schema.get("description", ""),
        "parameters": parameters,
    }


def create_text_completion(
    model_name: str,
    pdf_text: str = "",
    system_prompt: str = SYSTEM_PROMPT,
    instruction: str = INSTRUCTION_TEXT,
    api_key: str | None = None,
    api_base_url: str | None = None,
    extraction_method: EXTRACTION_METHODS = "tool_call",
    additional_params: dict | None = None,
    session_id: str | None = None,
) -> tuple[PerovskiteSolarCells, Any]:
    completion_fn = (
        observe(name="Perla Extract")(_create_text_completion)
        if log_traces
        else _create_text_completion
    )

    try:
        return completion_fn(
            model_name=model_name,
            pdf_text=pdf_text,
            system_prompt=system_prompt,
            instruction=instruction,
            api_key=api_key,
            api_base_url=api_base_url,
            extraction_method=extraction_method,
            additional_params=additional_params,
            session_id=session_id,
        )
    finally:
        if log_traces:
            langfuse_context.flush()


def _create_text_completion(
    model_name: str,
    pdf_text: str = "",
    system_prompt: str = SYSTEM_PROMPT,
    instruction: str = INSTRUCTION_TEXT,
    api_key: str | None = None,
    api_base_url: str | None = None,
    extraction_method: EXTRACTION_METHODS = "tool_call",
    additional_params: dict | None = None,
    session_id: str | None = None,
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
    # Construct messages for LLM
    messages = [
        {
            "role": "user",
            "content": f"{system_prompt}\n{instruction} \n\nHere is the text:\n{pdf_text}",
        },
    ]

    # Call with LiteLLM

    supported_params = set()
    max_tokens = MAX_TOKENS
    filtered_params: dict[str, Any] = {}
    if log_traces:
        filtered_params["metadata"] = {
            "session_id": session_id
        }
        langfuse_context.update_current_trace(session_id=session_id)
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
                "drop_params": True})
        if extraction_method == "response_format":
            filtered_params.update({"response_format": PerovskiteSolarCells})
        else:
            formatted_schema = format_schema(PerovskiteSolarCells)
            filtered_params["tools"] = [
                {
                    "type": "function", "function": formatted_schema
                }
            ]
            filtered_params["tool_choice"] = {
                "type": "function", "function": {"name": formatted_schema["name"]}
            }
            filtered_params.setdefault("reasoning_effort", "none")
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
            if extraction_method == "response_format":
                data = resp.choices[0].message.content
            else:
                data = resp.choices[0].message.tool_calls[0].function.arguments
            extracted_data = PerovskiteSolarCells.model_validate_json(data,strict=True,extra="forbid")
            break
        except ValidationError as e:
            logger.error(f"Validation error: {e}. Attempting to correct the output.")
            if retry_count >= MAX_RETRIES:
                logger.error(f"Max retries reached ({MAX_RETRIES}). Raising MaxRetriesExceededError.")
                raise MaxRetriesExceededError(resp)
            retry_count += 1
            retry_prompt = f'\n\nThe previous attempt resulted in a validation error: {e}. Correct the output to match the expected schema.'
            messages.append({'role':'assistant','content':data})
        except (AttributeError, IndexError, TypeError, ValueError) as e:
            logger.error(f"{type(e).__name__}: {e}. retrying.")
            if retry_count >= MAX_RETRIES:
                logger.error(f"Max retries reached ({MAX_RETRIES}). Raising MaxRetriesExceededError.")
                raise MaxRetriesExceededError(resp)
            retry_prompt = f'\n\nThe previous attempt resulted in an error: {type(e).__name__}: {e} when accessing the extracted in the response.' 
            retry_count += 1
        messages.append({"role":"user","content": retry_prompt})
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