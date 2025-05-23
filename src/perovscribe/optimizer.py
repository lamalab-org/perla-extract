from pydantic import BaseModel, Field
from litellm import completion
import instructor
from litellm.caching.caching import Cache
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.postprocessing import postprocess
import json
import os
from pathlib import Path
from perovscribe import llm_call
from perovscribe.export import to_json
from perovscribe.evaluations import score_multiple_extractions
import litellm
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
import numpy as np

from perovscribe.constants import (
    OPTIMIZER_PROMPT,
    STATE_TEMPLATE,
)

litellm.cache = Cache(
    type="redis",
    host="127.0.0.1",
    port=6379,
    ttl=1000000,
    password="foobared",
    namespace="litellm",
)


class OptimizerStep(BaseModel):
    new_prompt: str = Field(
        None,
        description="The modified prompt after the action you took on the old prompt.",
    )
    action: str = Field(
        None, description="The action you took on the old prompt to get the new prompt."
    )


class State(BaseModel):
    state: int = Field(None, description="The state, sorta like an ID, number.")
    action: str = Field(
        None, description="The action the LLM took to reach this state of the prompt."
    )
    prompt: str = Field(None, description="The prompt.")
    precision: float = Field(
        None, description="The precision results for extractions with this prompt."
    )
    recall: float = Field(
        None, description="The recall results for extractions with this prompt."
    )

    def get_state_template(self) -> str:
        return (
            STATE_TEMPLATE.replace("[state]", str(self.state))
            .replace("[action]", str(self.action))
            .replace("[prompt]", str(self.prompt))
            .replace("[precision]", str(self.precision))
            .replace("[recall]", str(self.recall))
        )


class States(BaseModel):
    states: list[State] = Field(
        [State(state=0, action="start", prompt="", precision=0.0, recall=0.0)],
        description="The states.",
    )

    def get_states_template(self) -> str:
        return "".join([state.get_state_template() for state in self.states])

    def add_state(self, action, prompt, precision, recall):
        self.states.append(
            State(
                state=self.states[-1].state + 1,
                action=action,
                prompt=prompt,
                precision=precision,
                recall=recall,
            )
        )


def call_llm(model_name: str, states):
    # Construct messages for LLM
    messages = [
        {"role": "system", "content": OPTIMIZER_PROMPT},
        {
            "role": "user",
            "content": f"History\n{states.get_states_template()}",
        },
    ]

    # Call with Instructor
    client = instructor.from_litellm(completion)

    resp = client.chat.completions.create(
        model=model_name,
        messages=messages,
        response_model=OptimizerStep,
        temperature=0.0,
        # cache={"no-cache": True}
    )

    return resp


def extract_all_for_prompt(prompt, model_name, output):
    output_folder = output + os.sep + model_name
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    preprocessor = Preprocessor("pymupdf", cache_dir_root="", use_cache=True)
    filepath = "../../../selecting_papers/15_pdfs/selected_training_set"
    og_prompt = prompt
    for file in [x for x in os.listdir(filepath) if x.endswith(".pdf")]:
        output = (
            output_folder
            + os.sep
            + os.path.splitext(os.path.basename(file))[0]
            + ".json"
        )
        pdf_text = preprocessor.pdf_to_text(filepath + os.sep + file)
        prompt = og_prompt.replace("[text]", pdf_text)
        prompt = (
            prompt.replace("[schema]", str(PerovskiteSolarCells.model_json_schema()))
            if "[schema]" in prompt
            else prompt
        )
        results = llm_call.create_text_completion(model_name, instruction=prompt)
        to_json(results, output)


def score_all(model_name, output):
    filepath = output + os.sep + model_name
    truthpath = "../../15_selected_papers"
    truth_extraction_pairs = []
    for file in [x for x in os.listdir(truthpath) if x.endswith(".json")]:
        with open(filepath + os.sep + file) as f:
            extraction = postprocess(json.load(f))
        with open(truthpath + os.sep + file) as f:
            truth = postprocess(json.load(f))

        truth_extraction_pairs.append((truth, extraction, file))

    list_of_evals, per_key_metrics = score_multiple_extractions(truth_extraction_pairs)
    precs = []
    recalls = []
    for index, evals in enumerate(list_of_evals):
        recalls.append(evals.recalls_average)
        precs.append(evals.precisions_average)

    # Calculate values for plot
    def calculate_value_for_plot(metrics_dict):
        def calculate_and_aggregate_precision(metrics_dict):
            # First calculate precision for all keys
            precision_results = {}

            for key, values in metrics_dict.items():
                tp = values.get("TP", 0.0)
                fp = values.get("FP", 0.0)

                # Calculate precision, handling division by zero
                if tp + fp > 0:
                    precision = tp / (tp + fp)
                    precision_results[key] = precision
                else:
                    continue

            # Initialize our aggregated results dictionary
            aggregated_results = {}

            # Find and aggregate keys ending with ":unit"
            unit_keys = [key for key in precision_results if key.endswith(":unit")]
            if unit_keys:
                unit_values = [precision_results[key] for key in unit_keys]
                aggregated_results["units"] = sum(unit_values) / len(unit_values)

            # Find and aggregate keys containing "composition"
            composition_keys = [
                key for key in precision_results if "composition" in key.lower()
            ]
            if composition_keys:
                composition_values = [
                    precision_results[key] for key in composition_keys
                ]
                print([(key, precision_results[key]) for key in composition_keys])
                aggregated_results["composition"] = sum(composition_values) / len(
                    composition_values
                )

            # Find and aggregate keys containing "stability"
            stability_keys = [
                key for key in precision_results if "stability" in key.lower()
            ]
            if stability_keys:
                stability_values = [precision_results[key] for key in stability_keys]
                aggregated_results["stability"] = sum(stability_values) / len(
                    stability_values
                )

            # Find and aggregate keys containing "deposition"
            deposition_keys = [
                key for key in precision_results if "deposition" in key.lower()
            ]
            if deposition_keys:
                deposition_values = [precision_results[key] for key in deposition_keys]
                aggregated_results["deposition"] = sum(deposition_values) / len(
                    deposition_values
                )

            # Find and aggregate keys containing "layers"
            layers_keys = [key for key in precision_results if "layers" in key.lower()]
            if layers_keys:
                layers_values = [precision_results[key] for key in layers_keys]
                aggregated_results["layers"] = sum(layers_values) / len(layers_values)

            # Find and aggregate keys containing "layers"
            light_keys = [key for key in precision_results if "light" in key.lower()]
            if light_keys:
                light_values = [precision_results[key] for key in light_keys]
                aggregated_results["light"] = sum(light_values) / len(light_values)

            # Add keys that don't match any of our aggregation rules, except "averaged_quantities"
            keys_to_exclude = set(
                unit_keys
                + composition_keys
                + stability_keys
                + deposition_keys
                + layers_keys
                + light_keys
            )
            for key in precision_results:
                if (
                    key not in keys_to_exclude
                    and "averaged_quantities" not in key
                    and "number_devices" not in key
                    and "encapsulated" not in key
                ):
                    key_lhs = key.replace("_", " ")
                    if ":value" in key:
                        aggregated_results[key_lhs[0 : key_lhs.rfind(":value")]] = (
                            precision_results[key]
                        )
                    else:
                        aggregated_results[key_lhs] = precision_results[key]

            return aggregated_results

        # Example usage with your sample data
        precision_results = calculate_and_aggregate_precision(metrics_dict)
        return precision_results

    return np.mean(precs), np.mean(recalls), calculate_value_for_plot(per_key_metrics)


def run(model_name, output):
    states = States()
    precision = 0.0
    from collections import defaultdict

    precision_results_lists = defaultdict(list)
    while precision < 0.98 and len(states.states) <= 6:
        resp = call_llm(model_name="claude-3-5-sonnet-20240620", states=states)

        import sys

        old_stdout = sys.stdout  # backup current stdout
        sys.stdout = open(os.devnull, "w")

        extract_all_for_prompt(resp.new_prompt, model_name, output)
        precision, recall, precision_results = score_all(model_name, output)

        sys.stdout = old_stdout  # reset old stdout

        states.add_state(resp.action, resp.new_prompt, precision, recall)
        print(f"{states.states[-1].get_state_template()}", recall)
        all_keys = [
            "units",
            "composition",
            "stability",
            "deposition",
            "layers",
            "light",
            "pce",
            "jsc",
            "voc",
            "ff",
            "active area",
            "device architecture",
        ]
        # NOTE: You need to process keys here otherwise you will have empty marker on your line plot
        for key in precision_results.keys():
            all_keys.remove(key)
            precision_results_lists[key].append(precision_results[key])
        print(
            precision_results.values(),
            list(precision_results_lists.values()),
            list(precision_results.keys()),
        )
