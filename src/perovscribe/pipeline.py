from pathlib import Path
from typing import Optional, Union
import os
import json
import glob
import math
import csv
import requests
from collections import defaultdict
import re
from importlib.resources import files

import numpy as np
from pydantic import ValidationError
import pdf2doi

from perovscribe.pydantic_model_reduced import PerovskiteSolarCells
from perovscribe.papersbot.utils import get_doi_summary
from perovscribe.preprocessing.preprocessor import Preprocessor
from perovscribe.postprocessing import postprocess
from perovscribe.evaluations import Evaluations, score_multiple_extractions
from perovscribe import llm_call
from perovscribe.export import (
    to_json,
    convert_extraction_to_nomad_entries,
    push_to_nomad,
    get_authentication_token,
)
from instructor.exceptions import InstructorRetryException, IncompleteOutputException

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


def get_doi_crossref_data(doi):
    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = requests.get(url, timeout=10)

        response.raise_for_status()
        data = response.json()["message"]
        journal = data.get("container-title", ["Unknown Journal"])[0]
        publisher = data.get("publisher", "Unknown Publisher")
        title = data.get("title", "")[0]
        abstract = data.get("abstract", None) or get_doi_summary(doi).get(
            "abstract", ""
        )
        return title, abstract, journal, publisher
    except requests.exceptions.HTTPError as e:
        print(f"Error fetching DOI {doi}: {e}")
        return None, None, None, None


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
        print(f"Could not extract DOI from {filepath}: {e}")
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
        nomad: bool = False,
        nomad_upload_id: str = None,
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

    def extract_from_pdf_nomad(
        self, filepath, api_key, doi=None, ureg=None
    ) -> Optional[PerovskiteSolarCells]:
        # We can use this in Nomad
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        if doi is None:
            doi = extract_doi_from_pdf(filepath)
        results, completion_usage = llm_call.create_text_completion(
            self.model_name, pdf_text, api_key=api_key
        )
        results = PerovskiteSolarCells(**postprocess(results.model_dump()))
        return convert_extraction_to_nomad_entries(results, doi, pdf_text, ureg=ureg)

    def _extract_pdf(self, filepath: Path, output_path: Path) -> bool:
        doi = extract_doi_from_pdf(filepath)
        pdf_text = self.preprocessor.pdf_to_text(filepath)
        if not self.is_doi_good_to_go(doi, pdf_text):
            print(f"This DOI {doi} will be skipped.")
            return False

        print(
            "Extracting:",
            (doi if doi != "NOT_FOUND" else filepath.stem.replace("--", "/")),
        )
        results = ""
        try:
            results, completion_usage = llm_call.create_text_completion(
                self.model_name, pdf_text
            )
            parsed = PerovskiteSolarCells(**postprocess(results.model_dump()))
            to_json(parsed, output_path)
            self.total_prompt_tokens += completion_usage.usage.prompt_tokens
            self.total_completion_tokens += completion_usage.usage.completion_tokens
            print(f"Extracted: {filepath.name}")
            if self.nomad:
                converted_nomad = convert_extraction_to_nomad_entries(
                    parsed, doi, pdf_text
                )
                push_to_nomad(
                    doi, converted_nomad, get_authentication_token(), self.upload_id
                )
            return True
        except (InstructorRetryException, ValidationError) as e:
            self._handle_failure(e, results, output_path)
        except (IncompleteOutputException, json.decoder.JSONDecodeError):
            output_path.write_text("")

        return False

    def is_doi_good_to_go(self, doi, pdf_text) -> bool:
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
                    files("perovscribe").joinpath("allowed_journals.csv")
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

        def non_solar_filter(text):
            non_solar_keywords = {
                "LED": [
                    r"\bLED\b",
                    r"light\s*-?\s*emitting\s+diode",
                    r"electroluminescen\w*",
                ],
                "Battery": [r"\bbattery\b", r"energy storage", r"rechargeable"],
                "Photodetector": [r"photodetector", r"X\s*-?\s*ray detector"],
                "Catalyst": [
                    r"catalys\w*",
                    r"photocatalys\w*",
                    r"water splitting",
                    r"hydrogen evolution",
                ],
                "Other": [
                    r"sens\w*",
                    r"sensor",
                    r"transistor",
                    r"laser",
                    r"memory",
                    r"thermoelectric",
                    r"capacitor",
                ],
            }

            solar_cell_keywords = [
                r"solar cell",
                r"photovoltaic",
                r"\bPV\b",
                r"\bPSC\b",
                r"\bPCE\b",
            ]

            # Compile patterns
            exclude_patterns = [
                re.compile(p, re.IGNORECASE)
                for sublist in non_solar_keywords.values()
                for p in sublist
            ]

            include_patterns = [
                re.compile(p, re.IGNORECASE) for p in solar_cell_keywords
            ]

            # Count matches
            non_solar_count = sum(len(p.findall(text)) for p in exclude_patterns)

            solar_count = sum(len(p.findall(text)) for p in include_patterns)

            # Decision rule:
            # 1. Must mention solar at least once
            # 2. Solar mentions must be >= non-solar mentions
            return solar_count > 0 and solar_count >= non_solar_count

        def theory_filter(text):
            theory_keywords = [
                r"\bDFT\b",
                r"\bSCAPS\b",
                r"\bSCAPS-1D\b",
                r"density functional",
                r"first.?principles",
                r"ab.?initio",
                r"molecular dynamics",
                r"\bMD\b simulation",
                r"VASP",
                r"Gaussian",
                r"Quantum ESPRESSO",
                r"CASTEP",
                r"SIESTA",
                r"computational study",
                r"theoretical study",
                r"theoretical investigation",
                r"numerical simulation",
                r"numerical investigation",
                r"device simulation",
                r"theoretical analysis",
                r"computational analysis",
                r"theoretical modell?ing",
                r"computational modell?ing",
                r"simulated",
                r"simulation of",
                r"wxAMPS",
                r"AMPS-1D",
                r"PC1D",
                r"AFORS-HET",
                r"theoretical optimization",
                r"computational optimization",
                r"numerical modeling",
                r"simulated performance",
                r"theoretical efficiency",
                r"predicted efficiency",
                r"simulation",
                r"\bMD\b.*simulation",
                # --- Machine Learning / AI ---
                r"machine learning",
                r"\bML\b",
                r"deep learning",
                r"\bDL\b",
                r"artificial intelligence",
                r"\bAI\b",
                r"neural network",
                r"neural networks",
                r"\bNN\b",
                r"\bANN\b",
                r"convolutional neural network",
                r"\bCNN\b",
                r"recurrent neural network",
                r"\bRNN\b",
                r"graph neural network",
                r"\bGNN\b",
                r"support vector machine",
                r"\bSVM\b",
                r"random forest",
                r"decision tree",
                r"k[- ]?nearest neighbors?",
                r"\bKNN\b",
                r"Gaussian process",
                r"\bGP\b",
                r"data[- ]?driven",
                r"surrogate model",
                r"meta[- ]?model",
                r"predictive model",
                r"statistical learning",
                r"learning[- ]?based",
                r"model training",
                r"model prediction",
                r"feature engineering",
                r"dimensionality reduction",
            ]
            pattern = re.compile("|".join(theory_keywords), re.IGNORECASE)
            match = pattern.search(text)
            return match is None

        def review_article_filter(text):
            review_patterns = [
                r"^Review\b",
                r"^Perspective\b",
                r"^Overview\b",
                r"^Outlook\b",
                r"^Minireview\b",
                r"^Critical [Rr]eview\b",
                r": [Aa] [Rr]eview\b",
                r": [Aa] [Pp]erspective\b",
                r"\b[Rr]eview of\b",
                r"\b[Rr]eview on\b",
                r"^Progress in\b",
                r"^Recent [Aa]dvances\b",
                r"^Advances in\b",
                r"^State of the art\b",
                r"^Current status\b",
                # Explicit review types
                r"\b(review|minireview|perspective|overview)\b",
                r"\bcritical review\b",
                r"\bstate[- ]of[- ]the[- ]art\b",
                r"\bis (discussed|reviewed|summarized|outlined)\b",
                r"\bare reviewed\b",
                r"\bwe review\b",
                r"\bthis (review|work) reviews\b",
            ]
            pattern = re.compile("|".join(review_patterns), re.IGNORECASE)
            is_review_article = pattern.search(text)
            return not is_review_article

        # If DOI could not be extracted, skip Crossref lookup and use only text-based filters
        if doi == "NOT_FOUND":
            text_to_filter = pdf_text[: int(len(pdf_text) * 0.05)]
            return (
                theory_filter(text_to_filter)
                and non_solar_filter(text_to_filter)
                and review_article_filter(text_to_filter)
            )

        title, abstract, journal, publisher = get_doi_crossref_data(doi)

        if abstract is None:
            abstract = ""
        
        if title is None:
            title = ""

        if word_count(abstract) < 100:
            text_to_filter = pdf_text[: int(len(pdf_text) * 0.05)]
        else:
            text_to_filter = title + " " + abstract

        if journal is None:
            return (
                theory_filter(text_to_filter)
                and non_solar_filter(text_to_filter)
                and review_article_filter(text_to_filter)
            )

        return (
            journal_filter(journal, publisher)
            and theory_filter(text_to_filter)
            and non_solar_filter(text_to_filter)
            and review_article_filter(text_to_filter)
        )

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
                print(e)
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
                    print(count)
        print("Prompt Tokens:", self.total_prompt_tokens)
        print("Completion Tokens:", self.total_completion_tokens)

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
    model_name: str = "claude-sonnet-4-20250514",
    preprocessor: str = "pymupdf",
    postprocessor: str = "NONE",
    cache_dir: str = "",
    use_cache: bool = False,
    pdf_print: bool = False,
    output: str = "./extractions",
    nomad: bool = False,
    nomad_upload_id: str = None,
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
        nomad,
        nomad_upload_id,
    ).run(filepath, truth, output=output)


def optimizer(model_name: str = "claude-sonnet-4-20250514", output: str = "./"):
    from perovscribe.optimizer import run

    run(model_name, output)
    # OptimizationPipeline(model_name).run(filepath)


def papersbot():
    if "UNPAYWALL_EMAIL" not in os.environ:
        print(
            "You need to provide your email for unpaywall API. Set this env variable: export UNPAYWALL_EMAIL=<your-email>"
        )
        return
    from perovscribe.papersbot import papersbot

    papersbot.run_papersbot()


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

        Path("./downloaded_papers/").mkdir(parents=True, exist_ok=True)
        # Download PDFs
        papersbot()
        # Extract them
        extract("./downloaded_papers")
        # Delete all PDFs
        files = glob.glob("./downloaded_papers/*.pdf")
        for f in files:
            os.remove(f)


def main_cli():
    import fire

    fire.Fire(CLI)


if __name__ == "__main__":
    main_cli()
