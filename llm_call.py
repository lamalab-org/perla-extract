import instructor
from anthropic import Anthropic
from process_pdf import encode_image_to_base64
from typing import List
import os

api_key = os.getenv("ANTHROPIC_API_KEY")

if not api_key:
    raise ValueError(
        "API key not found. Please set the ANTHROPIC_API_KEY environment variable."
    )

SYSTEM_PROMPT = "You are a world class AI that excels at extracting data about perovskite solar cells from papers. You only report single junction solar cells and no other types of solar cells. You never invent data and only state data that have been measured in the paper and which you can confidently extract. It is better for you to skip than to report data you are uncertain in. Take care to separate devices. Do not extract data people took from other papers but only data reported for the first time in this paper. Do not convert units yourself and stick to the units reported in the paper. Be careful with decimal points. Do not try to come up with a value by doing maths. Stick to what is reported. Be careful that the data you put together really belongs to the same device."
INSTRUCTION_TEXT_VISION = "Extract the data from the images of the paper. Do only report data about devices for which you are certain that the extraction you provide is correct. Do not convert any value or unit."
INSTRUCTION_TEXT = "Extract the data from the text of the paper. Do only report data about devices for which you are certain that the extraction you provide is correct. Do not convert any value or unit."

from cache import instructor_cache


def anthropic_call(
    model, text: str, images: List[str] = None, vision_model: bool = False
):
    client = instructor.from_anthropic(Anthropic())
    message_content = []
    if vision_model:
        for i, image in enumerate(images):
            image1_data = encode_image_to_base64(image)
            message_content.append(
                {"role": "user", "type": "text", "content": f"Image {i}:"}
            )
            message_content.append(
                {
                    "role": "user",
                    "type": "image",
                    "content": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image1_data,
                    },
                }
            )
        message_content.append(
            {"role": "user", "type": "text", "content": INSTRUCTION_TEXT_VISION}
        )
    else:
        message_content.append(
            {"role": "user", "type": "text", "content": INSTRUCTION_TEXT}
        )
        message_content.append({"role": "user", "type": "text", "content": text})

    resp = client.messages.create(
        model="claude-3-5-sonnet-20240620",
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=message_content,
        response_model=model,
        temperature=0,
    )
    return resp
