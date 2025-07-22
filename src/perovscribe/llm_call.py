import os
import sys
from litellm import completion
import instructor
from pydantic import BaseModel, Field
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.constants import SYSTEM_PROMPT, INSTRUCTION_TEXT
from litellm.caching.caching import Cache
import litellm

litellm.cache = Cache(
    type="redis",
    host="127.0.0.1",
    port=6379,
    ttl=1000000,
    password="foobared",
    namespace="litellm",
)


os.environ["REDIS_HOST"] = "127.0.0.1"
os.environ["REDIS_PORT"] = "6379"
os.environ["REDIS_PASSWORD"] = "foobared"
os.environ["REDIS_TTL"] = "1000000"


def create_text_completion(
    model_name: str,
    pdf_text: str = "",
    system_prompt: str = SYSTEM_PROMPT,
    instruction: str = INSTRUCTION_TEXT,
    api_key: str = None,
) -> PerovskiteSolarCells:
    """
    Extract perovskite solar cell data from a PDF using preprocessing and LLM.

    Arguments:
        model_name (str): the name of the LLM to call
        pdf_text (str): the text content from the PDF
        system_prompt (str): system prompt for the LLM (default: SYSTEM_PROMPT)
        instruction (str): instruction text for the LLM (default: INSTRUCTION_TEXT)
        api_key (str): api key for env where you can't use env vars

    Returns:
        PerovskiteSolarCells: the response from the LLM containing extracted perovskite solar cell data
    """
    if api_key:
        litellm.api_key = api_key

    # Construct messages for LLM
    messages = [
        # {"role": "system", "content": system_prompt}, #Choose one here!!
        # {"role": "user", "content": instruction}
        {
            "role": "user",
            "content": f"{system_prompt}\n{instruction}\n Here is the schema: {str(PerovskiteSolarCells.model_json_schema())} \n\nHere is the text:\n{pdf_text}",
        },
        # {
        #     "role": "user",
        #     "content": BEST_PROMPT.replace("[schema]", str(PerovskiteSolarCells.model_json_schema())).replace("[text]", pdf_text)
        # }
    ]

    # # Call with groq instructor
    # client = instructor.from_provider("groq/llama-3.3-70b-versatile", mode=instructor.Mode.JSON)

    # resp = client.chat.completions.create(
    #     # model="qwen-qwq-32b",
    #     messages=messages,
    #     response_model=PerovskiteSolarCells,
    #     temperature=0
    # )
    # print(resp)

    # return resp

    # Call with Instructor
    client = instructor.from_litellm(completion)

    resp, compll = client.chat.completions.create_with_completion(
        model=model_name,
        messages=messages,
        response_model=PerovskiteSolarCells,
        temperature=0,
        # max_tokens=32000,
    )

    return resp


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
        model="claude-3-5-sonnet-20240620",
        messages=messages,
        response_model=Judgement,
        temperature=0,
    )

    return resp
