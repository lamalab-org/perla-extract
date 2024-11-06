from litellm import completion
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import List

def create_text_completion(model_name: str, messages: List[dict]) -> str:
    """Call LLM via the LiteLLM using the standard pydantic model

    Arguments:
        model_name (str): the name of the LLM to call
        message (List[str]): messages to send to the LLM.
            Each dict should contain "role" and "content" keys

    Returns:
        str: the response of the LLM
    """
    return completion(
        model=model_name,
        messages=messages,
        response_format=PerovskiteSolarCells
    )
