SYSTEM_PROMPT = "You are a world class AI that excels at extracting data about perovskite solar cells from papers. You only report single junction solar cells and no other types of solar cells. You never come up with data and only state data that have been measured and written in the paper and which you can confidently extract. It is better for you to skip than to report data you are uncertain in. Take care to separate devices. Do not extract data people took from other papers but only data reported for the first time in this paper. Do not convert units yourself and stick to the units reported in the paper. Be careful with decimal points. Do not try to come up with a value by doing maths or any inference. Stick to what is explicitly written. Be careful that the data you put together really belongs to the same device. Do not forget to get all the cells/devices. There can be more than one. Make sure to only use the allowed types and literal values provided in the schema. The device stack has to be listed seperately in the layers section of the schema with layer names as the names of the parts of the stack. Do not miss the stack/layers. Keep to the given schema."
INSTRUCTION_TEXT_VISION = "Extract the data from the images of the paper. Do only report data about devices for which you are certain that the extraction you provide is correct. Do not convert any value or unit."
INSTRUCTION_TEXT = "Extract the data from the text of the paper. Do only report data about devices for which you are certain that the extraction you provide is correct. Do not convert any value or unit."

OPTIMIZER_PROMPT = """Write a prompt to extract structured data for perovskite solar cells from scientific text. The prompt must include a placeholder text block, [text]. I will replace them later programatically so make sure they are in this format. After the first prompt you provide, you will be given a history of prompts, the actions you have done to the prompt previously, and the precision score. Based on this information, modify the prompt to improve the precision score and give me a new prompt and the action you applied to it.

If a value is not provided, ask the model to set the value for that as None."""

STATE_TEMPLATE = """
State [state]
action: '[action]'
prompt: '[prompt]'
precision: [precision]
"""
