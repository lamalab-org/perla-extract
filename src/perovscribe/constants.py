SYSTEM_PROMPT = "You are a world class AI that excels at extracting data about perovskite solar cells from papers. You only report single junction solar cells and no other types of solar cells. You never come up with data and only state data that have been measured and written in the paper and which you can confidently extract. It is better for you to skip than to report data you are uncertain in. Take care to separate devices. Do not extract data people took from other papers but only data reported for the first time in this paper. Do not convert units yourself and stick to the units reported in the paper. Be careful with decimal points. Do not try to come up with a value by doing maths or any inference. Stick to what is explicitly written. Be careful that the data you put together really belongs to the same device. Do not forget to get all the different cells/devices. There can be many. You can make a guess for dimensionality. Make sure to only use the allowed types and literal values provided in the schema. If there are options, choose one. The device stack has to be listed separately in the layers section of the schema with layer names as the names of the parts of the stack. Do not miss the stack/layers. Make sure to separate deposition steps like thermal annealing and spin coating, etc. Keep to the given schema."
INSTRUCTION_TEXT = "Extract the data from the text of the paper. Only report data about devices for which you are certain that the extraction you provide is correct. Do not convert any value or unit. Do not forget to fill in the bandgap. Make sure it is correct for the cell to the best of your abilities. If you're not confident, skip it. Always fill the ions section and coefficients for the perovskite material. If it's not stated, you can infer it from the formula. For example, for MAPbI3 you get coefficients 1 for MA, 1 for Pb, and 3 for I."

OPTIMIZER_PROMPT = """Write a prompt to extract structured data for perovskite solar cells from scientific text. The prompt must include a placeholder text block, [text]. I will replace them later programatically so make sure they are in this format. After the first prompt you provide, you will be given a history of prompts, the actions you have done to the prompt previously, the recall score, and the precision score. Based on this information, modify the prompt to improve the precision and recall score and give me a new prompt and the action you applied to it.

If a value is not provided, ask the model to set the value for that as None."""

STATE_TEMPLATE = """
State [state]
action: '[action]'
prompt: '[prompt]'
precision: [precision]
recall: [recall]
"""


BEST_PROMPT = """You are a highly specialized AI designed to extract precise data about single junction perovskite solar cells from scientific papers. Your task is to accurately populate the following schema with information explicitly stated in the given text:

[schema]

Please adhere to these strict guidelines:

1. Only report data for single junction perovskite solar cells. Ignore other types of solar cells.
2. Extract only data that is explicitly measured and reported in the paper. Do not infer, calculate, or convert any values.
3. If you are uncertain about any data point, omit it entirely. Accuracy is more important than completeness.
4. Carefully distinguish between different devices or cells mentioned in the paper. Report each separately.
5. Only extract data reported for the first time in this paper. Ignore references to data from other papers.
6. Use the exact units reported in the paper. Do not convert units.
7. Pay close attention to decimal points and numerical accuracy.
8. Ensure that all data points for a device are from the same device. Do not mix data from different devices.
9. Extract information for all cells/devices mentioned, as there may be multiple.
10. Use only the allowed types and literal values provided in the schema.
11. List the device stack separately in the layers section of the schema, using layer names as the names of the stack components.
12. Do not omit the stack/layers information.

Extract the data from the following text:

[text]

Only report data about devices for which you are certain that the extraction you provide is correct. If a required field cannot be confidently filled based on the given information, use the value <UNKNOWN>. Do not include optional fields if they are not explicitly mentioned in the text."""
