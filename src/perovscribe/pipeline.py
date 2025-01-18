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
            to_json(results, output)
            postprocess(results.model_dump())
        elif ".json" in filepath:
            results = json.load(open(filepath, "r"))
            results = postprocess(results)
            evals = Evaluations(postprocess(json.load(open(truthpath))), results)
            print("==========================================")
            print("Score:", evals.score)
            print("Devices in truth:", evals.devices_in_truth)
            print("Devices found:", evals.devices_found)
            print("Devices matched:", evals.devices_matched)
            print("Device recall:", evals.recall_devices)
            print("Device stack score:", evals.score_device_stacks)
            print("Device layers score:", evals.score_device_layers)
            print("Precisions:", evals.score_precisions)
            print("Details:", evals.detailed_score)
        elif os.path.isdir(filepath) and os.path.isdir(truthpath):
            truth_extraction_pairs = []
            for file in [x for x in os.listdir(filepath) if x.endswith(".json")]:
                with open(filepath + os.sep + file) as f:
                    extraction = json.load(f)
                with open(truthpath + os.sep + file) as f:
                    truth = json.load(f)
                truth_extraction_pairs.append((truth, extraction))
            precs = []
            import numpy as np

            for evals in score_multiple_extractions(truth_extraction_pairs):
                print("==========================================")
                print("Score:", evals.score)
                print("Devices in truth:", evals.devices_in_truth)
                print("Devices found:", evals.devices_found)
                print("Devices matched:", evals.devices_matched)
                print("Device recall:", evals.recall_devices)
                print("Device stack score:", evals.score_device_stacks)
                print("Device layers score:", evals.score_device_layers)
                print("Precisions:", evals.score_precisions)
                print("Details:", evals.detailed_score)
                precs.append(np.mean(evals.score_precisions))
            print("Overall avg precision:", np.mean(precs))
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
):
    ExtractionPipeline(
        model_name, preprocessor, postprocessor, cache_dir, use_cache
    ).run(filepath, truth)


def main_cli():
    import fire

    fire.Fire(extract)
