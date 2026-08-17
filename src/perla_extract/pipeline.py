from pathlib import Path
from typing import Optional, Union
import os
import json
import glob
import math
import csv
from collections import defaultdict
import re
from importlib.resources import files
import time
import numpy as np
from pydantic import ValidationError
import pdf2doi

from perla_extract.configuration import papersbot_runs_path, EXTRACTION_METHODS, DEFAULT_LLM_MODEL
from perla_extract.pydantic_model_reduced import PerovskiteSolarCells
from perla_extract.papersbot.utils import get_doi_summary
from perla_extract.papersbot.papersbot import PapersbotResult
from perla_extract.preprocessing.preprocessor import Preprocessor
from perla_extract.postprocessing import postprocess
from perla_extract.evaluations import Evaluations, score_multiple_extractions
from perla_extract import llm_call
from perla_extract.llm_call import MaxRetriesExceededError
from perla_extract.export import (
    to_json,
    convert_extraction_to_nomad_entries,
    push_to_nomad,
    get_authentication_token,
)
from loguru import logger
from perla_extract.llm_classifier import classify_paper
pdf2doi.config.set("verbose", False)


def calc_precision(per_key_metrics, key):
    return per_key_metrics[key]["TP"] / (
        per_key_metrics[key]["TP"] + per_key_metrics[key]["FP"]
    )


def calculate_and_aggregate(metrics_dict, compute_recall=True):
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


def read_csv_to_dict(filepath):
    with open(filepath, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        return [row for row in reader]


def log_processing(doi, key, value):
    log_path =Path(f"{papersbot_runs_path}/found_pdf_urls.json")
    folder_path = log_path.parent
    if not folder_path.exists():
        return
    if log_path.exists():
        with open(log_path, "r") as f:
                log_json = json.load(f)
    else:
        log_json = {}
    if doi in log_json:
        log_json[doi][key] = value
    else:
        log_json[doi] = {key: value}
    with open(log_path, "w") as f:
        json.dump(log_json, f, indent=4)
    
def is_doi_good_to_go(doi, pdf_text, metadata=None) -> bool:
    def remove_conjunctions(text):
        # Convert to lowercase
        text = text.lower()

        # Replace HTML entity &amp; with &
        text = text.replace("&amp;", "&")

        # Remove 'and' as a word and '&' symbols with optional spaces around them
        # This also handles cases like "Tom&Jerry" or "Tom & Jerry"
        text = re.sub(r"\b(and)\b", "", text)
        text = re.sub(r"\s*&\s*", " ", text)

        # Clean up any extra whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text
    
    def journal_filter(journal, publisher):
        words_not_allowed = [
            "reviews",
            "theory",
            "computation",
            "catalysis",
            "review",
            "ceramic",
            "toxicology",
            "bio",
        ]
        if any(word in journal.lower() for word in words_not_allowed):
            return False

        allowed_journals = [
            journal_entry["Source title"]
            for journal_entry in read_csv_to_dict(
                files("perla_extract").joinpath("allowed_journals.csv")
            )
        ]
        if journal not in allowed_journals:
            return remove_conjunctions(journal) in [
                remove_conjunctions(j) for j in allowed_journals
            ]

        return True
    
    def extract_words(text):
        # 1. Remove HTML tags (e.g., <a href="...">)
        text = re.sub(r"<[^>]+>", " ", text)

        # 2. Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()

        # 3. Extract words (letters, numbers, underscores)
        words = re.findall(r"\b\w+\b", text)

        return words


    def word_count(text):
        return len(extract_words(text))

    metadata = metadata or get_doi_summary(doi)['consolidated']
    for key in ['title', 'journal', 'abstract']:
        if key not in metadata or metadata[key] is None:
            metadata[key] = ""
    abstract = metadata.get("abstract", "")
    journal = metadata.get("journal", "")
    publisher = metadata.get("publisher", "")

    if journal and not journal_filter(journal, publisher):
        logger.info(f"Paper {doi} filtered out due to journal: {journal}")
        return False
        
    if word_count(abstract) < 100 and pdf_text:
        for r in range(5, 0, -1):
            abstract = pdf_text[:int(len(pdf_text) * (r / 100))]
            if word_count(abstract) <= 300:
                break
        metadata['abstract'] = abstract

    classification_result = classify_paper(metadata)
    if classification_result is None:
        return False
    elif not classification_result.label:
        logger.info(f"Paper {doi} classified as not relevant: {classification_result.reason}")
        return False
    
    return True

def extract_doi_from_pdf(filepath) -> str:
    doi = "NOT_FOUND"
    try:
        pdf2doi_results = pdf2doi.pdf2doi(filepath)
        if pdf2doi_results is None:
            return doi
        pdf2doi_results = (
            pdf2doi_results[0] if isinstance(pdf2doi_results, list) else pdf2doi_results
        )
        if pdf2doi_results.get("identifier_type") == "DOI":
            doi = pdf2doi_results.get("identifier", doi)
    except Exception as e:
        logger.error(f"Could not extract DOI from {filepath}: {e}")
    return doi


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data.

    Initialize the extraction pipeline.

    Args:
        model_name (str): name of the LLM to call
        preprocessor (str): the preprocessor to use
        postprocessor (str): the postprocessor to use
        cache_dir (Union[Path, str]): the root directory for the diskcache
        use_cache (bool): True if caching should be utilized
        nomad (bool): True if exporting to NOMAD
        nomad_upload_id (str): upload ID for NOMAD export
    """

    def __init__(
        self,
        model_name: str,
        preprocessor: str,
        postprocessor: str,
        cache_dir: Union[Path, str],
        use_cache: bool = True,
        infer_doi: bool = False,
        nomad: bool = False,
        nomad_upload_id: str = None,
        extraction_method: EXTRACTION_METHODS = "tool_call",
        additional_params: dict = None,
    ):
        self.model_name = model_name
        self.preprocessor = Preprocessor(
            preprocessor, cache_dir_root=cache_dir, use_cache=use_cache
        )
        self.postprocessor = ...  # call postprocessing factory to obtain postprocessor
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.nomad = nomad
        self.upload_id = nomad_upload_id
        self.infer_doi = infer_doi
        self.extraction_method = extraction_method
        self.additional_params = additional_params


    def extract_from_pdf_nomad(
        self, filepath, api_key, doi=None, ureg=None, api_base_url=None
    ) -> Optional[PerovskiteSolarCells]:
        # We can use this in Nomad
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        if doi is None:
            doi = extract_doi_from_pdf(filepath)
        results, completion_usage = llm_call.create_text_completion(
            self.model_name, pdf_text, api_key=api_key, api_base_url=api_base_url
        )
        results = PerovskiteSolarCells(**postprocess(results.model_dump()))
        return convert_extraction_to_nomad_entries(results, doi, pdf_text,model_name=self.model_name, ureg=ureg)

    def _extract_pdf(self, filepath: Path, output_path: Path) -> bool:
        if not self.infer_doi:
            doi = extract_doi_from_pdf(filepath)
        else:
            doi = filepath.stem.replace("--", "/")
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        if not is_doi_good_to_go(doi, pdf_text):
            logger.info(f"This DOI {doi} ({filepath.name}) will be skipped.")
            log_processing(doi, 'skipped', True)
            log_processing(doi, 'processed', True)
            return False
        log_msg = doi if doi != "NOT_FOUND" else filepath.stem.replace('--', '/')
        logger.info(
            f"Extracting: {log_msg}"
        )

        try:
            session_id = f"{doi}-{self.model_name.replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}"
            results, completion_usage = llm_call.create_text_completion(
                self.model_name, pdf_text, extraction_method=self.extraction_method, additional_params=self.additional_params, session_id=session_id
            )
            parsed = PerovskiteSolarCells(**postprocess(results.model_dump()))
            to_json(parsed, output_path)
            self.total_prompt_tokens += completion_usage.usage.prompt_tokens
            self.total_completion_tokens += completion_usage.usage.completion_tokens
            logger.info(f"Extracted: {filepath.name}")    
            log_processing(doi, 'extracted', True)
            if self.nomad:
                converted_nomad = convert_extraction_to_nomad_entries(parsed, doi, pdf_text, self.model_name)
                with open(str(output_path).replace(".json", "_nomad.json"), "w") as f:
                    json.dump(converted_nomad, f, indent=2)
                log_processing(doi, 'n_cells_extracted', len(converted_nomad))
                push_to_nomad(doi, converted_nomad, get_authentication_token(), self.upload_id)
                log_processing(doi, 'nomad_upload_processed', True)
            log_processing(doi, 'processed', True)
            return True
        except ValidationError as e:
            self._handle_failure(e, results, output_path)
        except MaxRetriesExceededError as e:
            self._handle_failure(e, e.args[0], output_path)
        except json.decoder.JSONDecodeError as e:
            output_path.write_text("")

        return False

    def _run_single_pdf(self, filepath: Path, output_dir: Path):
        output_path = output_dir / f"{filepath.stem}.json"
        self._extract_pdf(filepath, output_path)

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
                logger.error(e)
                continue

        evals_list, key_metrics = score_multiple_extractions(pairs)
        recalls, precs, llm_calls = [], [], 0

        for evals in evals_list:
            precs.append(np.mean(evals.score_precisions))
            recalls.append(np.mean(evals.score_recalls))
            llm_calls += evals.llm_judge_calls

        print(json.dumps(key_metrics, indent=2))
        # agg_results = calculate_value_for_plot(key_metrics)
        # print("Aggregated Metrics:", json.dumps(agg_results, indent=2))
        print("Average Recall:", np.nanmean(recalls))
        print("Average Precision:", np.nanmean([p for p in precs if not math.isnan(p)]))
        print("LLM Judge Calls:", llm_calls)
        avg_recalls = np.nanmean(recalls)
        avg_precisions = np.nanmean([p for p in precs if not math.isnan(p)])
        return key_metrics, avg_recalls, avg_precisions

    def _extract_batch(self, input_dir: Path, output_dir: Path):
        (output_dir / self.model_name).mkdir(parents=True, exist_ok=True)
        count = 0
        already_extracted = [
            x.stem for x in (input_dir / "extractions" / self.model_name).glob("*.json")
        ]
        for pdf_file in input_dir.glob("*.pdf"):
            if pdf_file.stem not in already_extracted:
                output_path = output_dir / self.model_name / f"{pdf_file.stem}.json"
                if self._extract_pdf(pdf_file, output_path):
                    count += 1
                    logger.info(f"Extracted {count} PDFs so far.")
        logger.info(f"Extraction completed. Total PDFs extracted: {count}")
        logger.info(f"Prompt Tokens: {self.total_prompt_tokens}")
        logger.info(f"Completion Tokens: {self.total_completion_tokens}")

    def _handle_failure(self, error, results, output_path):
        logger.error(f"Extraction failed: {error}")
        try:
            processed = postprocess(results.model_dump())
        except Exception as e:
            processed = {"raw_output": str(results.choices[0].message)}
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
            logger.error(f"Unsupported input: {filepath}")


def extract(
    filepath: str,
    truth: str = None,
    model_name: str = DEFAULT_LLM_MODEL,
    preprocessor: str = "pymupdf",
    postprocessor: str = "NONE",
    cache_dir: str = "",
    use_cache: bool = False,
    pdf_print: bool = False,
    output: str = "./extractions",
    infer_doi: bool = False,
    nomad: bool = False,
    nomad_upload_id: str = None,
    extraction_method: EXTRACTION_METHODS = "tool_call",
    additional_params: dict = None,
):
    if pdf_print:
        print(
            Preprocessor(
                preprocessor, cache_dir_root=cache_dir, use_cache=use_cache
            ).pdf_to_text(filepath)
        )
        return
    ExtractionPipeline(
        model_name,
        preprocessor,
        postprocessor,
        cache_dir,
        use_cache,
        infer_doi,
        nomad,
        nomad_upload_id,
        extraction_method,
        additional_params
    ).run(filepath, truth, output=output)


def optimizer(model_name: str = "claude-sonnet-4-20250514", output: str = "./"):
    from perla_extract.optimizer import run

    run(model_name, output)
    # OptimizationPipeline(model_name).run(filepath)


def papersbot(download_dir: str = "./downloaded_papers") -> PapersbotResult:
    """Run papersbot workflow to download papers.
    
    Args:
        download_dir: Directory to download PDFs to (default: "./downloaded_papers")
        
    Returns:
        PapersbotResult with execution status and results
    """
    if "UNPAYWALL_EMAIL" not in os.environ:
        return PapersbotResult(
            success=False,
            error="UNPAYWALL_EMAIL environment variable not set"
        )
    
    try:
        from perla_extract.papersbot import papersbot as papersbot_module
        # Pass download_dir to the module
        result = papersbot_module.run_papersbot(download_dir=download_dir)
        return result
    except Exception as e:
        return PapersbotResult(
            success=False,
            error=f"Papersbot execution failed: {str(e)}"
        )


def evaluate(
    extraction_dir: str,
    truth_dir: str,
    model_name: str = "claude-sonnet-4-20250514",
):
    """Evaluate extraction results against ground truth.

    Args:
        extraction_dir: Directory containing extraction JSON files
        truth_dir: Directory containing ground truth JSON files
        model_name: Model name (used for consistency, not for extraction)
    """
    ExtractionPipeline(model_name, "pymupdf", "NONE", "", False, False, None).run(
        extraction_dir, truth_dir
    )


class CLI:
    """Command line interface for extraction and optimization."""

    def __init__(self):
        self.extract = extract
        self.evaluate = evaluate
        self.optimizer = optimizer
        self.papersbot = papersbot

    def __call__(self, *args, **kwargs):
        """Default behavior when no command is specified."""
        download_path = Path("./downloaded_papers").resolve()
        download_path.mkdir(parents=True, exist_ok=True)
        
        # Run papersbot
        result = papersbot(download_dir=str(download_path))
        
        if not result.success:
            logger.error(f"Papersbot failed: {result.error}")
            return
        
        logger.info(f"Found {result.papers_found} papers, downloaded {result.pdfs_downloaded} PDFs")
        
        # Extract them if PDFs were downloaded
        if result.pdfs_downloaded > 0:
            extract(str(download_path))
        
        # Delete all PDFs
        files = glob.glob(f"{download_path}/*.pdf")
        for f in files:
            os.remove(f)


def main_cli():
    import fire

    fire.Fire(CLI)


if __name__ == "__main__":
    main_cli()
