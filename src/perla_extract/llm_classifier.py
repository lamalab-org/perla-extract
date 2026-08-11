from litellm import completion
from pydantic import BaseModel

from perla_extract.configuration import CLASSIFIER_MODEL
from perla_extract.constants import LLM_CLASSIFIER_PROMPT, LLM_CLASSIFIER_USER_PROMPT

from loguru import logger

class PaperFilter(BaseModel):
    label: bool
    reason: str

def classify_paper(paper: dict, model: str=CLASSIFIER_MODEL) -> PaperFilter | None:
    for key in ['title', 'journal', 'abstract']:
        if key not in paper or not paper[key]:
            logger.warning(f'Missing "{key}" in paper metadata dictionary.')
            paper[key] = 'Not Available'
    prompt = LLM_CLASSIFIER_USER_PROMPT.format(
        TITLE=paper.get('title', 'Not Available'), JOURNAL=paper.get('journal', 'Not Available'), ABSTRACT=paper.get('abstract', 'Not Available')
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
        logger.error(f'Error occurred: {e}')
    return None