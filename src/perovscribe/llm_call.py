import instructor
from anthropic import Anthropic
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
import json

def create_text_completion(
    model_name: str,
    pdf_text: str,
    system_prompt: str = SYSTEM_PROMPT,
    instruction: str = INSTRUCTION_TEXT,
) -> PerovskiteSolarCells:  
    """
    Extract perovskite solar cell data from a PDF using preprocessing and LLM.
    
    Arguments:
        model_name (str): the name of the LLM to call
        pdf_text (str): the text content from the PDF
        system_prompt (str): system prompt for the LLM (default: SYSTEM_PROMPT)
        instruction (str): instruction text for the LLM (default: INSTRUCTION_TEXT)
    
    Returns:
        PerovskiteSolarCells: the response from the LLM containing extracted perovskite solar cell data
    """
    # Construct messages for LLM
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{instruction}\n\nHere is the text:\n{pdf_text}"}
    ]
    
    # Call LLM via LiteLLM
    # response_json = json.loads(completion(
    #     model=model_name,
    #     messages=messages,
    #     response_format=PerovskiteSolarCells
    # )["choices"][0]["message"]["content"])
    # return PerovskiteSolarCells(**response_json)

    # Call with Instructor
    client = instructor.from_anthropic(Anthropic())

    # note that client.chat.completions.create will also work
    resp = client.messages.create(
        model=model_name,
        max_tokens=8192,
        messages=messages,
        response_model=PerovskiteSolarCells,
        temperature=0
    )

    return resp
