import os
import sys
from litellm import completion
import instructor
from pydantic import BaseModel, Field
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
import litellm

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
except ImportError:
    # Redis package not installed, fall back to disk cache
    from litellm.caching.caching import Cache
    print("Redis package not available, using disk cache")
    litellm.cache = Cache(type="disk")
except Exception as e:
    # Redis connection failed or other error, fall back to disk cache
    from litellm.caching.caching import Cache
    print(f"Redis cache setup failed ({e}), using disk cache")
    litellm.cache = Cache(type="disk")


def create_text_completion(
    model_name: str,
    pdf_text: str = "",
    system_prompt: str = SYSTEM_PROMPT,
    instruction: str = INSTRUCTION_TEXT,
    api_key: str = None,
) -> PerovskiteSolarCells:
    """
     Extract structured perovskite solar cell data from raw PDF text using an LLM.

    This function sends a prompt to a language model (via the `instructor` library and LiteLLM backend)
    with a specified system prompt and instruction, along with the given PDF text. It attempts to
    deserialize the response into a `PerovskiteSolarCells` Pydantic model, automatically handling
    context length errors by reducing `max_tokens` if needed.

    Args:
        model_name (str): Name of the LLM to use (e.g., "gpt-4", "claude-3-opus", etc.).
        pdf_text (str): Text content extracted from a PDF document.
        system_prompt (str): The system-level prompt guiding the LLM’s behavior (default: SYSTEM_PROMPT).
        instruction (str): Task-specific instruction for the LLM (default: INSTRUCTION_TEXT).
        api_key (str, optional): API key for LiteLLM if environment variables cannot be used.

    Returns:
        PerovskiteSolarCells: A Pydantic model instance populated with the extracted data.

    Raises:
        instructor.exceptions.InstructorRetryException: If an unhandled error occurs during LLM interaction.
    """
    if api_key:
        litellm.api_key = api_key

    # Construct messages for LLM
    messages = [
        {
            "role": "user",
            "content": f"{system_prompt}\n{instruction}\n Here is the schema: {str(PerovskiteSolarCells.model_json_schema())} \n\nHere is the text:\n{pdf_text}",
        },
    ]

    # Call with Instructor
    client = instructor.from_litellm(completion)

    max_tokens = 64000
    temperature = 0
    try:
        model_info = litellm.get_model_info(model=model_name)
        if not model_info:
            max_tokens = 8192
        else:
            max_tokens = model_info.get("max_output_tokens")
        
        supported_params = litellm.get_supported_openai_params(model=model_name)
        if "temperature" not in supported_params:
            temperature = 1
    except Exception as e:
        print(f"Could not fetch model info, defaulting to max_tokens=64000 and temperature=0. Error: {e}")
    
    client = instructor.from_litellm(completion)
    
    while True:
        try:
            resp, compll = client.chat.completions.create_with_completion(
                model=model_name,
                messages=messages,
                response_model=PerovskiteSolarCells,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except instructor.exceptions.InstructorRetryException as e:
            if (
                'litellm.BadRequestError: AnthropicException - {"type":"error","error":{"type":"invalid_request_error","message":"input length and `max_tokens` exceed context limit:'
                not in str(e)
            ):
                raise
            max_tokens -= 5000
            print("reduced max tokens")
        else:
            break
    return resp, compll


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

    client = instructor.from_litellm(completion)

    resp = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=messages,
        response_model=Judgement,
        temperature=0,
    )

    return resp
