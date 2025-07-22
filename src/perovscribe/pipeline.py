from pathlib import Path
from typing import Optional, Union
import os
import json
import glob
import math
from collections import defaultdict

import numpy as np
from pydantic import ValidationError

from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.postprocessing import postprocess
from perovscribe.evaluations import Evaluations, score_multiple_extractions
from perovscribe import llm_call
from perovscribe.export import to_json, convert_to_extraction_to_nomad_entries
from instructor.exceptions import InstructorRetryException, IncompleteOutputException


def calc_precision(per_key_metrics, key):
    return per_key_metrics[key]["TP"] / (
        per_key_metrics[key]["TP"] + per_key_metrics[key]["FP"]
    )


def calculate_and_aggregate(metrics_dict, compute_recall=False):
    result = {}
    for key, vals in metrics_dict.items():
        tp, fp, fn = vals.get("TP", 0.0), vals.get("FP", 0.0), vals.get("FN", 0.0)
        if compute_recall:
            result[key] = tp / (tp + fn) if tp + fn > 0 else np.nan
        else:
            result[key] = tp / (tp + fp) if tp + fp > 0 else np.nan

    # Aggregation groups
    aggregations = {
        "units": lambda k: k.endswith(":unit"),
        "composition": lambda k: "composition" in k.lower(),
        "stability": lambda k: "stability" in k.lower(),
        "deposition": lambda k: "deposition" in k.lower(),
        "layers": lambda k: "layers" in k.lower(),
        "light": lambda k: "light" in k.lower(),
    }

    aggregated = {}
    for label, condition in aggregations.items():
        values = [v for k, v in result.items() if condition(k)]
        if values:
            aggregated[label] = np.nanmean(values)

    # Add remaining keys
    excluded_keys = {k for group in aggregations.values() for k in result if group(k)}
    for k, v in result.items():
        if k not in excluded_keys and not any(
            x in k for x in ["averaged_quantities", "number_devices", "encapsulated"]
        ):
            clean_key = k.replace("_", " ").split(":value")[0]
            aggregated[clean_key] = v

    return aggregated


def calculate_value_for_plot(metrics_dict):
    precision_results = {
        paper: calculate_and_aggregate(metrics)
        for paper, metrics in metrics_dict.items()
    }
    return precision_results


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

    def extract_from_pdf_nomad(
        self, filepath, doi, api_key
    ) -> Optional[PerovskiteSolarCells]:
        # We can use this in Nomad
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        results = llm_call.create_text_completion(self.model_name, pdf_text, api_key)
        results = PerovskiteSolarCells(**postprocess(results.model_dump()))
        return convert_to_extraction_to_nomad_entries(
            results, os.path.splitext(os.path.basename(filepath))[0]
        )

    def _run_single_pdf(self, filepath: Path, output_dir: Path):
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        results = llm_call.create_text_completion(self.model_name, pdf_text)
        parsed = PerovskiteSolarCells(**postprocess(results.model_dump()))
        nomad_entry = convert_to_extraction_to_nomad_entries(parsed, filepath.stem)
        print(nomad_entry)

    def _evaluate_single_json(self, filepath: Path, truthpath: Path):
        pred = postprocess(json.load(open(filepath)))
        truth = postprocess(json.load(open(truthpath)))
        evals = Evaluations(
            truth, pred, filepath, defaultdict(lambda: defaultdict(float))
        )
        print("Evaluation Results:")
        print(f"Score: {evals.score}")
        print(f"Precision Avg: {evals.precisions_average}")
        print(f"Recall Avg: {evals.recalls_average}")

    def _evaluate_multiple(self, pred_dir: Path, truth_dir: Path):
        pairs = []
        for file in truth_dir.glob("*.json"):
            try:
                pred_path = pred_dir / file.name
                pred = postprocess(json.load(open(pred_path)))
                truth = postprocess(json.load(open(file)))
                pairs.append((truth, pred, file.name))
            except (FileNotFoundError, json.decoder.JSONDecodeError) as e:
                print(e)
                continue

        evals_list, key_metrics = score_multiple_extractions(pairs)
        recalls, precs, llm_calls = [], [], 0

        for evals in evals_list:
            precs.append(np.mean(evals.score_precisions))
            recalls.append(np.mean(evals.score_recalls))
            llm_calls += evals.llm_judge_calls

        print(json.dumps(key_metrics, indent=2))
        agg_results = calculate_value_for_plot(key_metrics)
        print("Aggregated Metrics:", json.dumps(agg_results, indent=2))
        print("Average Recall:", np.nanmean(recalls))
        print("Average Precision:", np.nanmean([p for p in precs if not math.isnan(p)]))
        print("LLM Judge Calls:", llm_calls)

    def _extract_batch(self, input_dir: Path, output_dir: Path):
        (output_dir / self.model_name).mkdir(parents=True, exist_ok=True)
        for pdf_file in input_dir.glob("*.pdf"):
            output_path = output_dir / self.model_name / f"{pdf_file.stem}.json"
            pdf_text = self.preprocessor.pdf_to_text(pdf_file)
            try:
                results = llm_call.create_text_completion(self.model_name, pdf_text)
                parsed = PerovskiteSolarCells(**postprocess(results.model_dump()))
                to_json(parsed, output_path)
                print("Extracted:", pdf_file)
            except (InstructorRetryException, ValidationError) as e:
                self._handle_failure(e, results, output_path)
            except IncompleteOutputException:
                output_path.write_text("")
            except json.decoder.JSONDecodeError:
                output_path.write_text("")

    def _handle_failure(self, error, results, output_path):
        print(f"Extraction failed: {error}")
        try:
            processed = postprocess(results.model_dump())
        except Exception:
            processed = json.loads(
                error.last_completion.choices[0]
                .message.tool_calls[0]
                .function.arguments
            )
        output_path.write_text(json.dumps(processed, indent=2))

    def run(
        self,
        filepath: Union[Path, str],
        truthpath: Union[Path, str],
        output: Union[Path, str] = "./extractions",
    ):
        filepath = Path(filepath)
        truthpath = Path(truthpath) if truthpath else None
        output = Path(output)

        if filepath.suffix == ".pdf":
            self._run_single_pdf(filepath, output)
        elif filepath.suffix == ".json":
            self._evaluate_single_json(filepath, truthpath)
        elif filepath.is_dir() and truthpath:
            self._evaluate_multiple(filepath, truthpath)
        elif filepath.is_dir():
            self._extract_batch(filepath, output)
        else:
            print(f"Unsupported input: {filepath}")


def extract(
    filepath: str,
    truth: str = None,
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
