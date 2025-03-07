from typing import Optional
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from typing import Union
from pathlib import Path
import json
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.postprocessing import postprocess
from perovscribe.evaluations import Evaluations, score_multiple_extractions
from perovscribe import llm_call
from perovscribe.export import to_json
import os
import csv


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

    def extract_from_pdf(
        self, filepath: Union[Path, str]
    ) -> Optional[PerovskiteSolarCells]: ...

    def run(
        self,
        filepath: Union[Path, str],
        truthpath: Union[Path, str],
        output: Union[Path, str] = "./",
    ):
        if ".pdf" in filepath:
            output = output + os.path.splitext(os.path.basename(filepath))[0] + ".json"
            pdf_text = self.preprocessor.pdf_to_text(filepath)
            results = llm_call.create_text_completion(self.model_name, pdf_text)
            # to_json(results, output) # Add back for regular
            with open(output, "w") as f:
                f.write(json.dumps(results))
            # postprocess(results.model_dump()) # TODO: Check if you want this to be done
        elif ".json" in filepath:
            results = json.load(open(filepath, "r"))
            results = postprocess(results)
            evals = Evaluations(
                postprocess(json.load(open(truthpath))), results, filepath
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
            print("Recalls:", evals.score_recalls)
        elif os.path.isdir(filepath) and os.path.isdir(truthpath):
            truth_extraction_pairs = []
            for file in [x for x in os.listdir(truthpath) if x.endswith(".json")]:
                with open(filepath + os.sep + file) as f:
                    extraction = postprocess(json.load(f))
                with open(truthpath + os.sep + file) as f:
                    # Note: Postprocessing has an effect on the llm judge call. I prefer now not to add this device stack
                    truth = postprocess(json.load(f))

                truth_extraction_pairs.append((truth, extraction, file))
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
                print("Recalls:", evals.score_recalls)
                precs.append(np.mean(evals.score_precisions))
                recalls.append(np.mean(evals.score_recalls))
                llm_judge_calls += evals.llm_judge_calls
                total_missing_devices += max(
                    0, evals.devices_in_truth - evals.devices_found
                )

            print(
                "Total active area:",
                per_key_metrics["active_area:value"]["FN"]
                + per_key_metrics["active_area:value"]["TP"]
                + per_key_metrics["active_area:value"]["FP"]
                + total_missing_devices,
            )
            print(
                "metric_keys",
                len(per_key_metrics.keys()),
            )
            print(
                "Important Precisions:",
                "FF:",
                calc_precision(per_key_metrics, "ff:value"),
                "PCE:",
                calc_precision(per_key_metrics, "pce:value"),
                "jsc:",
                calc_precision(per_key_metrics, "jsc:value"),
                "voc:",
                calc_precision(per_key_metrics, "voc:value"),
            )
            fields = ["Fields", "TP", "FP", "FN"]
            with open("per_key_metrics.csv", "w") as f:
                f.write("Fields, TP, FP, FN\n")
                w = csv.DictWriter(f, fields)
                for key, val in sorted(per_key_metrics.items()):
                    row = {"Fields": key}
                    row.update(val)
                    w.writerow(row)
            print(
                "LLM Judge calls average:",
                llm_judge_calls / len(truth_extraction_pairs),
            )
            print("Overall avg recalls:", np.mean(recalls))
            print("Overall avg precision:", np.mean(precs))
        elif os.path.isdir(filepath):
            output_folder = output + os.sep + self.model_name
            Path(output_folder).mkdir(parents=True, exist_ok=True)
            for file in [x for x in os.listdir(filepath) if x.endswith(".pdf")]:
                output = (
                    output_folder
                    + os.sep
                    + os.path.splitext(os.path.basename(file))[0]
                    + ".json"
                )
                pdf_text = self.preprocessor.pdf_to_text(filepath + os.sep + file)
                results = llm_call.create_text_completion(self.model_name, pdf_text)
                to_json(results, output)
                postprocess(results.model_dump())
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


def main_cli():
    import fire

    fire.Fire(extract)


if __name__ == "__main__":
    main_cli()
