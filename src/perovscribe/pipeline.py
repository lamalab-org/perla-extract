from typing import Optional
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from instructor.exceptions import InstructorRetryException
from pydantic import ValidationError
from typing import Union
from pathlib import Path
import json
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.postprocessing import postprocess
from perovscribe.evaluations import Evaluations, score_multiple_extractions
from perovscribe import llm_call
from perovscribe.export import to_json, convert_to_extraction_to_nomad_entries
import os
from collections import defaultdict
import glob


def calc_precision(per_key_metrics, key):
    return per_key_metrics[key]["TP"] / (
        per_key_metrics[key]["TP"] + per_key_metrics[key]["FP"]
    )


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data.

    Initialize the extraction pipeline.

    Args:
        model_name (str): name of the LLM to call
        preprocessor (str): the preprocessor to use
        postprocessor (str): the postprocessor to use
        cache_dir (Union[Path, str]): the root directory for the diskcache
        use_cache (bool): True if caching should be utilized
    """

    def __init__(
        self,
        model_name: str,
        preprocessor: str,
        postprocessor: str,
        cache_dir: Union[Path, str],
        use_cache: bool = True,
    ):
        self.model_name = model_name
        self.preprocessor = Preprocessor(
            preprocessor, cache_dir_root=cache_dir, use_cache=use_cache
        )
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_cache = use_cache

    def extract_from_pdf_nomad(self, filepath, doi) -> Optional[PerovskiteSolarCells]:
        # We can use this in Nomad
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        results = llm_call.create_text_completion(self.model_name, pdf_text)
        results = PerovskiteSolarCells(**postprocess(results.model_dump()))
        return convert_to_extraction_to_nomad_entries(
            results, os.path.splitext(os.path.basename(filepath))[0]
        )

    def run(
        self,
        filepath: Union[Path, str],
        truthpath: Union[Path, str],
        output: Union[Path, str] = "./extractions",
    ):
        if ".pdf" in filepath:
            output = output + os.path.splitext(os.path.basename(filepath))[0] + ".json"
            pdf_text = self.preprocessor.pdf_to_text(filepath)
            results = llm_call.create_text_completion(self.model_name, pdf_text)
            results = PerovskiteSolarCells(**postprocess(results.model_dump()))
            # to_json(results, output)  # Add back for regular
            # Test Nomad conversion:
            print(
                convert_to_extraction_to_nomad_entries(
                    results, os.path.splitext(os.path.basename(filepath))[0]
                )
            )
        elif ".json" in filepath:
            results = json.load(open(filepath, "r"))
            results = postprocess(results)
            evals = Evaluations(
                postprocess(json.load(open(truthpath))),
                results,
                filepath,
                defaultdict(lambda: defaultdict(float)),
            )
            print("==========================================")
            print("Score:", evals.score)
            print("Devices in truth:", evals.devices_in_truth)
            print("Devices found:", evals.devices_found)
            print("Devices matched:", evals.devices_matched)
            print("Device recall:", evals.recall_devices)
            print("Device stack score:", evals.score_device_stacks)
            print("Device layers score:", evals.score_device_layers)
            print("Precisions:", evals.score_precisions)
            print("Precision Avg:", evals.precisions_average)
            print("Recalls:", evals.score_recalls)
        elif os.path.isdir(filepath) and os.path.isdir(truthpath):
            truth_extraction_pairs = []
            for file in [x for x in os.listdir(truthpath) if x.endswith(".json")]:
                try:
                    with open(filepath + os.sep + file) as f:
                        extraction = postprocess(json.load(f))
                    with open(truthpath + os.sep + file) as f:
                        # Note: Postprocessing has an effect on the llm judge call. I prefer now not to add this device stack
                        truth = postprocess(json.load(f))

                    truth_extraction_pairs.append((truth, extraction, file))
                except FileNotFoundError:
                    pass
            precs = []
            recalls = []
            import numpy as np

            llm_judge_calls = 0

            list_of_evals, per_key_metrics = score_multiple_extractions(
                truth_extraction_pairs
            )
            total_missing_devices = 0
            for index, evals in enumerate(list_of_evals):
                print("==========================================")
                print(truth_extraction_pairs[index][2])
                print("Score:", evals.score)
                print("Devices in truth:", evals.devices_in_truth)
                print("Devices found:", evals.devices_found)
                print("Devices matched:", evals.devices_matched)
                print("Device recall:", evals.recall_devices)
                print("Device stack score:", evals.score_device_stacks)
                print("Device layers score:", evals.score_device_layers)
                print("Precisions:", evals.score_precisions)
                print("Precision Avg:", evals.precisions_average)
                print("Recalls:", evals.score_recalls)
                precs.append(np.mean(evals.score_precisions))
                recalls.append(np.mean(evals.score_recalls))
                llm_judge_calls += evals.llm_judge_calls
                total_missing_devices += max(
                    0, evals.devices_in_truth - evals.devices_found
                )

            # Calculate values for plot
            def calculate_value_for_plot(metrics_dict):
                def calculate_and_aggregate_precision(metrics_dict, compute_recall=False):
                    # First calculate precision for all keys
                    precision_results = {}

                    for key, values in metrics_dict.items():
                        tp = values.get("TP", 0.0)
                        fp = values.get("FP", 0.0)
                        fn = values.get("FN", 0.0)

                        if compute_recall:
                            if tp + fn > 0:
                                recall = tp / (tp + fn)
                                precision_results[key] = recall
                            else:
                                continue
                        else:
                            # Calculate precision, handling division by zero
                            if tp + fp > 0:
                                precision = tp / (tp + fp)
                                precision_results[key] = precision
                            else:
                                continue

                    # Initialize our aggregated results dictionary
                    aggregated_results = {}

                    # Find and aggregate keys ending with ":unit"
                    unit_keys = [
                        key for key in precision_results if key.endswith(":unit")
                    ]
                    if unit_keys:
                        unit_values = [precision_results[key] for key in unit_keys]
                        aggregated_results["units"] = sum(unit_values) / len(
                            unit_values
                        )

                    # Find and aggregate keys containing "composition"
                    composition_keys = [
                        key for key in precision_results if "composition" in key.lower()
                    ]
                    if composition_keys:
                        composition_values = [
                            precision_results[key] for key in composition_keys
                        ]
                        aggregated_results["composition"] = sum(
                            composition_values
                        ) / len(composition_values)

                    # Find and aggregate keys containing "stability"
                    stability_keys = [
                        key for key in precision_results if "stability" in key.lower()
                    ]
                    if stability_keys:
                        stability_values = [
                            precision_results[key] for key in stability_keys
                        ]
                        aggregated_results["stability"] = sum(stability_values) / len(
                            stability_values
                        )

                    # Find and aggregate keys containing "deposition"
                    deposition_keys = [
                        key for key in precision_results if "deposition" in key.lower()
                    ]
                    if deposition_keys:
                        deposition_values = [
                            precision_results[key] for key in deposition_keys
                        ]
                        aggregated_results["deposition"] = sum(deposition_values) / len(
                            deposition_values
                        )

                    # Find and aggregate keys containing "layers"
                    layers_keys = [
                        key for key in precision_results if "layers" in key.lower()
                    ]
                    if layers_keys:
                        layers_values = [precision_results[key] for key in layers_keys]
                        aggregated_results["layers"] = sum(layers_values) / len(
                            layers_values
                        )

                    # Find and aggregate keys containing "layers"
                    light_keys = [
                        key for key in precision_results if "light" in key.lower()
                    ]
                    if light_keys:
                        light_values = [precision_results[key] for key in light_keys]
                        aggregated_results["light"] = sum(light_values) / len(
                            light_values
                        )

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
                                aggregated_results[
                                    key_lhs[0 : key_lhs.rfind(":value")]
                                ] = precision_results[key]
                            else:
                                aggregated_results[key_lhs] = precision_results[key]

                    return aggregated_results

                # Example usage with your sample data
                precision_results = {}
                for index, evals in enumerate(list_of_evals):
                    precision_results[truth_extraction_pairs[index][2]] = (
                        calculate_and_aggregate_precision(
                            metrics_dict[truth_extraction_pairs[index][2]]
                        )
                    )
                # precision_results = calculate_and_aggregate_precision(metrics_dict)
                print("Precision:", precision_results)

                # recall_results = calculate_and_aggregate_precision(
                #     metrics_dict, recall=True
                # )
                # print("Recall:", recall_results)

            print(json.dumps(per_key_metrics))
            calculate_value_for_plot(per_key_metrics)

            print(
                "LLM Judge calls average:",
                llm_judge_calls / len(truth_extraction_pairs),
                "Total:",
                llm_judge_calls,
            )
            print("Overall avg recalls:", np.mean(recalls))

            import math

            precs = [value for value in precs if not math.isnan(value)]
            print("Overall avg precision:", np.mean(precs))
        elif os.path.isdir(filepath):
            output_folder = output + os.sep + self.model_name
            Path(output_folder).mkdir(parents=True, exist_ok=True)
            for file in [x for x in os.listdir(filepath) if x.endswith(".pdf")]:
                print("Filename: ", file)
                output = (
                    output_folder
                    + os.sep
                    + os.path.splitext(os.path.basename(file))[0]
                    + ".json"
                )
                pdf_text = self.preprocessor.pdf_to_text(filepath + os.sep + file)
                try:
                    results = llm_call.create_text_completion(self.model_name, pdf_text)
                    results = PerovskiteSolarCells(**postprocess(results.model_dump()))
                    # results = PerovskiteSolarCells(**postprocess(json.loads(results.choices[0].message.content)))
                    print(results)
                    to_json(results, output)
                except (InstructorRetryException, ValidationError) as e:
                    print(
                        e,
                        file + " failed!!!!!!",
                        # results.model_dump()
                    )
                    with open(output, "w") as f:
                        f.write(
                            json.dumps(
                                postprocess(
                                    json.loads(
                                        e.last_completion.choices[0]
                                        .message.tool_calls[0]
                                        .function.arguments
                                    )
                                )
                            )
                        )  # Make sure postprocessing is triggered when it fails
        else:
            print("Hmmm. This wasn't one of the expected inputs. Have a look again.")


def extract(
    filepath: str,
    truth: str = "",
    model_name: str = "claude-3-5-sonnet-20240620",
    preprocessor: str = "pymupdf",
    postprocessor: str = "NONE",
    cache_dir: str = "",
    use_cache: bool = True,
    pdf_print: bool = False,
):
    if pdf_print:
        print(
            Preprocessor(
                preprocessor, cache_dir_root=cache_dir, use_cache=use_cache
            ).pdf_to_text(filepath)
        )
        return
    ExtractionPipeline(
        model_name, preprocessor, postprocessor, cache_dir, use_cache
    ).run(filepath, truth)


def optimizer(model_name: str = "claude-3-5-sonnet-20240620", output: str = "./"):
    from perovscribe.optimizer import run

    run(model_name, output)
    # OptimizationPipeline(model_name).run(filepath)


def papersbot():
    if "UNPAYWALL_EMAIL" not in os.environ:
        print(
            "You need to provide your email for unpaywall API. Set this env variable: export UNPAYWALL_EMAIL=<your-email>"
        )
        return
    from perovscribe.papersbot import main as papersbot

    papersbot()


class CLI:
    """Command line interface for extraction and optimization."""

    def __init__(self):
        self.extract = extract
        self.optimizer = optimizer
        self.papersbot = papersbot

    def __call__(self, *args, **kwargs):
        """Default behavior when no command is specified."""
        Path("./downloaded_papers/").mkdir(parents=True, exist_ok=True)
        # Download PDFs
        papersbot()
        # Extract them
        extract("./downloaded_papers")
        # Delete all PDFs
        files = glob.glob("./download_papers/*.pdf")
        for f in files:
            os.remove(f)


def main_cli():
    import fire

    fire.Fire(CLI)


if __name__ == "__main__":
    main_cli()
