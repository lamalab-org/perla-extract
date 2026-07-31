from litellm import completion
from pydantic import BaseModel

from perla_extract.configuration import classifier_model
from perla_extract.constants import LLM_CLASSIFIER_PROMPT, LLM_CLASSIFIER_USER_PROMPT


class PaperFilter(BaseModel):
    label: bool
    reason: str

def classify_paper(paper: dict, model: str=classifier_model) -> PaperFilter | None:
    prompt = LLM_CLASSIFIER_USER_PROMPT.format(
        TITLE=paper.get('title', ''), JOURNAL=paper.get('journal', ''), ABSTRACT=paper.get('abstract', '')
    )
    messages = [
        {
            'role': 'system',
            'content': LLM_CLASSIFIER_PROMPT,
        },
        {'role': 'user', 'content': prompt},
    ]
    resp = completion(
        timeout=60, model=model, messages=messages, response_format=PaperFilter
    )

    content = resp.choices[0].message.content
    try:
        if isinstance(content, str):
        # content is a JSON string
            return PaperFilter.model_validate_json(content)
        else:
            # content is already a structured object (e.g., dict/model)
            return PaperFilter.model_validate(content)
    except Exception as e:
        print(f'Error occurred: {e}')
    return None