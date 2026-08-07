"""
Improved evaluation module for comparing structured datasets (e.g., solar cell data).

This module provides utilities to evaluate similarity between two structured datasets
using DeepDiff for deep distance computation and the Munkres algorithm for optimal matching.
"""

from typing import Any, Dict, List, Optional, Tuple, TypedDict
from copy import deepcopy
from collections import defaultdict
from collections.abc import Mapping

import numpy as np
from deepdiff import DeepDiff
from munkres import Munkres
from Levenshtein import distance, ratio
import flatdict

class Match(TypedDict):
    """Type definition for a matched pair of truth and extraction data."""

    truth: Dict[str, Any]
    extraction: Dict[str, Any]


class EvaluationMetrics:
    """
    Container for evaluation metrics and constants.
    """

    DEFAULT_PRECISION_TOLERANCES = {
        "pce": 0.1,  # 0.1 absolute tolerance
        "jsc": 0.1,  # 0.1 absolute tolerance
        "voc": 0.01,  # 0.01 absolute tolerance
        "ff": 0.1,  # 0.1 absolute tolerance
    }

    DEEPDIFF_CONFIG = {
        "get_deep_distance": True,
        "ignore_string_case": True,
        "ignore_string_type_changes": True,
        "ignore_numeric_type_changes": True,
        "significant_digits": 2,
        "number_format_notation": "e",
        "cutoff_intersection_for_pairs": 1,
    }


class Evaluations:
    """
    Utility class to evaluate similarity between two structured datasets.

    Computes a similarity score from 0 to 1 where 1 indicates highest similarity.
    Uses DeepDiff for deep distance computation and Munkres algorithm for optimal matching.

    Note: Input datasets should be normalized with perla_extract.postprocessing.
    """

    def __init__(
        self,
        truth: Dict[str, Any],
        extraction: Dict[str, Any],
        file: str,
        per_key_metrics: Dict[str, Dict[str, float]],
        precision_tolerances: Optional[Dict[str, float]] = None,
        use_llm_judge: bool = False,
    ):
        self.precision_tolerances = (
            precision_tolerances or EvaluationMetrics.DEFAULT_PRECISION_TOLERANCES
        )
        self.use_llm_judge = use_llm_judge

        # Initialize basic metrics
        extraction = extraction if "cells" in extraction and extraction["cells"] is not None else {"cells":[]}
        self.devices_in_truth = len(truth["cells"])
        self.devices_found = len(extraction["cells"])

        # Calculate core metrics
        self.score = self._calculate_inverted_deepdiff(
            truth["cells"], extraction["cells"]
        )
        (
            self.matches,
            self.unmatched_truth_cells,
            self.unmatched_extraction_cells,
        ) = self._match_cells_with_unmatched(
            truth["cells"], extraction["cells"], file
        )
        self.devices_matched = len(self.matches)
        self.recall_devices = min(self.devices_matched / (len(truth["cells"]) or 1), 1)
        self.precision_devices = min(
            self.devices_matched / (len(extraction["cells"]) or 1), 1
        )

        # Calculate detailed scores
        self.score_device_stacks = self._score_device_stacks()
        self.score_device_layers = self._score_device_layers()

        self.score_precisions, self.llm_judge_calls = self._score_precisions(
            per_key_metrics
        )
        self.precisions_average = (
            float(np.mean(self.score_precisions)) if self.score_precisions else 0.0
        )

        self.score_recalls = self._score_recalls(per_key_metrics)
        self.recalls_average = (
            float(np.mean(self.score_recalls)) if self.score_recalls else 0.0
        )

    def _pad_extraction_for_missing_devices(
        self, extraction: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add empty devices to extraction for false negatives."""
        extraction = deepcopy(extraction)  # Don't modify original

        if self.devices_in_truth > self.devices_found:
            empty_devices = [
                {"layers": []}
                for _ in range(self.devices_in_truth - self.devices_found)
            ]
            extraction["cells"].extend(empty_devices)

        return extraction

    def _pad_missing_devices(self, scores: List[float]) -> List[float]:
        """Add zeros for devices missing in extraction."""
        missing_devices = max(0, self.devices_in_truth - self.devices_found)
        return scores + [0.0] * missing_devices

    def _calculate_inverted_deepdiff(self, lhs: Any, rhs: Any) -> float:
        """Calculate similarity score using inverted DeepDiff distance."""
        diff = DeepDiff(lhs, rhs, **EvaluationMetrics.DEEPDIFF_CONFIG)
        return 1.0 - diff.get("deep_distance", 0.0)

    def _score_device_stacks(self) -> List[float]:
        """Calculate scores for device stacks based on layer names."""
        scores = []
        for match in self.matches:
            truth_stack = " ".join(
                [
                    layer.get("name", "NOTRUTH")
                    for layer in match["truth"].get("layers", [])
                ]
            )
            extraction_stack = " ".join(
                [
                    layer.get("name", "NOEXTRACT") or "NOEXTRACT"
                    for layer in match["extraction"].get("layers", []) or []
                ]
            )
            scores.append(
                self._calculate_inverted_deepdiff(truth_stack, extraction_stack)
            )
        return scores

    def _score_device_layers(self) -> List[float]:
        """Calculate scores for device layers structure."""
        scores = []
        for match in self.matches:
            truth_layers = match["truth"]["layers"]
            extraction_layers = match["extraction"].get("layers", [])
            scores.append(
                self._calculate_inverted_deepdiff(truth_layers, extraction_layers)
            )
        return scores

    def _score_precisions(
        self, per_key_metrics: Dict[str, Dict[str, float]]
    ) -> Tuple[List[float], int]:
        """Calculate precision scores for all matches."""
        precisions = []
        llm_judge_calls = 0

        # Fix precision tolerances keys to access nested values
        fixed_tolerances = {
            f"{key}:value": tolerance
            for key, tolerance in self.precision_tolerances.items()
        }

        matches = self._prepare_matches_for_scoring()
        matches.extend(
            {"truth": {}, "extraction": deepcopy(cell)}
            for cell in self.unmatched_extraction_cells
        )
        for match in matches:
            precision, judge_calls = self._calculate_match_precision(
                match, fixed_tolerances, per_key_metrics
            )
            precisions.append(precision)
            llm_judge_calls += judge_calls

        return precisions, llm_judge_calls

    def _score_recalls(
        self, per_key_metrics: Dict[str, Dict[str, float]]
    ) -> List[float]:
        """Calculate recall scores for all matches."""
        recalls = []

        matches = self._prepare_matches_for_scoring()
        matches.extend(
            {"truth": deepcopy(cell), "extraction": {}}
            for cell in self.unmatched_truth_cells
        )
        for match in matches:
            recall = self._calculate_match_recall(match, per_key_metrics)
            recalls.append(recall)

        return recalls

    def _prepare_matches_for_scoring(self) -> List[Match]:
        """Prepare matches by matching layers within each match."""
        prepared_matches = []

        for match in self.matches:
            prepared_match = deepcopy(match)

            # Match layers within this device match
            truth_layers = prepared_match["truth"].get("layers", [])
            extraction_layers = prepared_match["extraction"].get("layers", [])

            (
                prepared_match["truth"]["layers"],
                prepared_match["extraction"]["layers"],
            ) = self._match_layers(truth_layers, extraction_layers)

            prepared_matches.append(prepared_match)

        return prepared_matches

    def _calculate_match_precision(
        self,
        match: Match,
        tolerances: Dict[str, float],
        per_key_metrics: Dict[str, Dict[str, float]],
    ) -> Tuple[float, int]:
        """Calculate precision for a single match."""
        found = []
        llm_judge_calls = 0

        flat_truth = self._flatten_non_null(match["truth"])
        flat_extraction = self._flatten_non_null(match["extraction"])

        # Precision is evaluated over facts the extractor actually emitted. An
        # emitted value with no corresponding ground-truth fact is a false
        # positive, rather than being silently ignored.
        for key, extracted_value in flat_extraction.items():
            if self._should_skip_key(key, {}):
                continue
            key_for_stats = self._regularize_repeated_key(key)
            if key in flat_truth:
                tolerance = tolerances.get(key_for_stats)
                is_correct = self._is_value_correct(
                    flat_truth[key],
                    extracted_value,
                    tolerance=tolerance if tolerance is not None else 0.01,
                    abs_tolerance=tolerance is not None,
                    key=key_for_stats,
                    per_key_metrics=per_key_metrics,
                )

                # Use LLM judge for string comparisons that failed initial check
                if self.use_llm_judge and not is_correct and self._is_key_judgable(
                    key, flat_truth, flat_extraction
                ):
                    # Keep the default evaluation deterministic and offline.
                    # The optional semantic judge is imported only when asked
                    # for so importing the scorer has no network side effects.
                    from perla_extract.llm_call import llm_as_judge

                    judgement = llm_as_judge(
                        match["truth"], flat_truth[key], flat_extraction[key]
                    )
                    llm_judge_calls += 1
                    is_correct = judgement.judgement
                    print(
                        f"LLM judge for {key}: {flat_truth[key]} vs {flat_extraction[key]} -> {is_correct}"
                    )

                if not is_correct:
                    self._log_precision_mismatch(
                        key, flat_truth[key], extracted_value, match
                    )
            else:
                is_correct = False
                per_key_metrics[key_for_stats]["scoring_method"] = (
                    "no ground-truth fact"
                )

            found.append(is_correct)
            self._update_precision_metrics(key_for_stats, is_correct, per_key_metrics)

        return sum(found) / max(len(found), 1), llm_judge_calls

    def _calculate_match_recall(
        self, match: Match, per_key_metrics: Dict[str, Dict[str, float]]
    ) -> float:
        """Calculate recall for a single match."""
        found = []

        flat_truth = self._flatten_non_null(match["truth"])
        flat_extraction = self._flatten_non_null(match["extraction"])

        for key, truth_value in flat_truth.items():
            if self._should_skip_key(key, {}):
                continue

            key_for_stats = self._regularize_repeated_key(key)
            if key in flat_extraction:
                tolerance = self.precision_tolerances.get(
                    key_for_stats.removesuffix(":value")
                )
                is_correct = self._is_value_correct(
                    truth_value,
                    flat_extraction[key],
                    tolerance=tolerance if tolerance is not None else 0.01,
                    abs_tolerance=tolerance is not None,
                    key=key_for_stats,
                    per_key_metrics=per_key_metrics,
                )
            else:
                is_correct = False

            found.append(is_correct)

            if not is_correct:
                per_key_metrics[key_for_stats]["FN"] += 1

        return sum(found) / len(found) if found else 0.0

    def _match_cells(
        self, truth_cells: List[Dict], extracted_cells: List[Dict], file: str
    ) -> List[Match]:
        """Match cells using optimal assignment based on functionality and structure."""
        matches, _, _ = self._match_cells_with_unmatched(
            truth_cells, extracted_cells, file
        )
        return matches

    def _match_cells_with_unmatched(
        self, truth_cells: List[Dict], extracted_cells: List[Dict], file: str
    ) -> Tuple[List[Match], List[Dict], List[Dict]]:
        """Match real cells and retain both kinds of unmatched device.

        Padding before assignment used to turn every missing device into a
        nominal match. Keeping unmatched cells explicit makes device recall and
        field-level false positives/negatives honest.
        """
        if not truth_cells or not extracted_cells:
            return [], deepcopy(truth_cells), deepcopy(extracted_cells)

        # Calculate functionality-based scores
        truth_functionalities = [
            self._extract_functionalities(cell) for cell in truth_cells
        ]
        extract_functionalities = [
            self._extract_functionalities(cell) for cell in extracted_cells
        ]

        stack_scores = [
            [
                self._score_functionalities(t_func, e_func)
                for e_func in extract_functionalities
            ]
            for t_func in truth_functionalities
        ]

        # Calculate deposition-based scores
        truth_depositions = [
            [layer.get("deposition") for layer in cell.get("layers", []) or []]
            for cell in truth_cells
        ]
        extracted_depositions = [
            [layer.get("deposition") for layer in cell.get("layers", []) or []]
            for cell in extracted_cells
        ]

        # Combine scores with weights
        combined_scores = [
            [
                (0.7 * stack_scores[i][j])
                + (
                    0.2
                    * -self._calculate_inverted_deepdiff(
                        truth_depositions[i], extracted_depositions[j]
                    )
                )
                + (
                    0.1
                    * -self._calculate_inverted_deepdiff(
                        truth_cells[i], extracted_cells[j]
                    )
                )
                for j in range(len(extracted_cells))
            ]
            for i in range(len(truth_cells))
        ]

        # Find optimal assignment
        munkres = Munkres()
        indexes = munkres.compute(combined_scores)

        matches = [
            {
                "truth": deepcopy(truth_cells[row]),
                "extraction": deepcopy(extracted_cells[col]),
            }
            for row, col in indexes
        ]
        truth_indexes = {row for row, _ in indexes}
        extraction_indexes = {col for _, col in indexes}
        return (
            matches,
            [deepcopy(cell) for i, cell in enumerate(truth_cells) if i not in truth_indexes],
            [
                deepcopy(cell)
                for i, cell in enumerate(extracted_cells)
                if i not in extraction_indexes
            ],
        )

    def _match_layers(
        self, truth_layers: List[Dict], extraction_layers: List[Dict]
    ) -> Tuple[List[Dict], List[Dict]]:
        """Match layers within a cell using functionality and name similarity."""
        truth_layers = deepcopy(truth_layers or [])
        extraction_layers = deepcopy(extraction_layers or [])
        if not truth_layers and not extraction_layers:
            return [], []

        # A square assignment retains extra extracted layers as false positives
        # and missing truth layers as false negatives.
        size = max(len(truth_layers), len(extraction_layers))
        truth_layers.extend({} for _ in range(size - len(truth_layers)))
        extraction_layers.extend({} for _ in range(size - len(extraction_layers)))

        # Calculate similarity scores
        scores = [
            [
                distance(
                    (t.get("functionality", "NOTRUTH") or "NOTRUTH")
                    + (t.get("name", "NOTRUTH") or "NOTRUTH"),
                    (e.get("functionality", "NOEXTRACT") or "NOEXTRACT")
                    + (e.get("name", "NOEXTRACT") or "NOEXTRACT"),
                )
                for e in extraction_layers
            ]
            for t in truth_layers
        ]

        # Find optimal assignment
        munkres = Munkres()
        indexes = munkres.compute(scores)

        # Match depositions within matched layers
        for row, col in indexes:
            truth_layers[row]["deposition"], extraction_layers[col]["deposition"] = (
                self._match_depositions(
                    truth_layers[row].get("deposition", [{}]),
                    extraction_layers[col].get("deposition", [{}]),
                )
            )

        return (
            [truth_layers[row] for row, _ in indexes],
            [extraction_layers[col] for _, col in indexes],
        )

    def _match_depositions(
        self, truth_depositions: List[Dict], extraction_depositions: List[Dict]
    ) -> Tuple[List[Dict], List[Dict]]:
        """Match deposition elements within layers."""
        truth_depositions = deepcopy(truth_depositions or [])
        extraction_depositions = deepcopy(extraction_depositions or [])
        if not truth_depositions and not extraction_depositions:
            return [], []

        size = max(len(truth_depositions), len(extraction_depositions))
        truth_depositions.extend({} for _ in range(size - len(truth_depositions)))
        extraction_depositions.extend(
            {} for _ in range(size - len(extraction_depositions))
        )

        # Calculate similarity scores based on method and temperature
        scores = [
            [
                distance(
                    (t.get("method", "NOTRUTH") or "NOTRUTH")
                    + str(
                        t.get("temperature", {}).get("value", "NOTRUTH")
                        if isinstance(t.get("temperature"), dict)
                        else "NOTRUTH"
                    ),
                    (e.get("method", "NOEXTRACT") or "NOEXTRACT")
                    + str(
                        e.get("temperature", {}).get("value", "NOEXTRACT")
                        if isinstance(e.get("temperature"), dict)
                        else "NOEXTRACT"
                    ),
                )
                for e in extraction_depositions
            ]
            for t in truth_depositions
        ]

        # Find optimal assignment
        munkres = Munkres()
        indexes = munkres.compute(scores)

        return (
            [truth_depositions[row] for row, _ in indexes],
            [extraction_depositions[col] for _, col in indexes],
        )

    # Helper methods
    def _flatten_non_null(self, value: Dict[str, Any]) -> Dict[str, Any]:
        """Return atomic, non-null facts using the scorer's flattened key form."""
        flattened = flatdict.FlatterDict(value)
        return {
            key: item
            for key, item in flattened.items()
            if item is not None and not isinstance(item, (Mapping, list))
        }

    def _extract_functionalities(self, cell: Dict) -> Dict[str, List[str]]:
        """Extract functionality mapping from a cell."""
        functionalities = defaultdict(list)
        for layer in cell.get("layers", []) or []:
            functionality = layer.get("functionality")
            name = layer.get("name", "incorrect") or "incorrect"
            functionalities[functionality].append(name)
        return functionalities

    def _score_functionalities(self, truth_funcs: Dict, extract_funcs: Dict) -> float:
        """Score functionality similarity between two cells."""
        total_distance = 0
        for func_name in truth_funcs:
            if func_name not in extract_funcs:
                extract_funcs[func_name] = ["NOTFOUND"]
            total_distance += -self._calculate_string_similarity(
                truth_funcs[func_name], extract_funcs[func_name]
            )
        return total_distance

    def _calculate_string_similarity(self, s1: List[str], s2: List[str]) -> float:
        """Calculate similarity between two lists of strings."""
        s1, s2 = s1.copy(), s2.copy()

        # Remove SLG if present
        for lst in [s1, s2]:
            try:
                lst.remove("SLG")
            except ValueError:
                pass

        total_similarity = 0.0
        for i, str1 in enumerate(s1):
            if i < len(s2):
                if " " in str1 or " " in s2[i]:
                    total_similarity += self._calculate_string_similarity(
                        str1.split(" "), s2[i].split(" ")
                    )
                else:
                    total_similarity += ratio(str1, s2[i])

        return total_similarity

    def _is_value_correct(
        self,
        truth: Any,
        extract: Any,
        tolerance: float = 0.01,
        abs_tolerance: bool = False,
        key="NO-KEY",
        per_key_metrics=defaultdict(lambda: defaultdict(float))
    ) -> bool:
        """Check if extracted value matches truth within tolerance."""
        if isinstance(truth, (int, float)):
            try:
                truth, extract = float(truth), float(extract)
            except (TypeError, ValueError):
                per_key_metrics[key]["scoring_method"] = "numeric type check"
                return False
            try:
                if abs_tolerance:
                    np.testing.assert_allclose(extract, truth, atol=tolerance, rtol=0)
                    per_key_metrics[key]["scoring_method"] = "float | abstol("+str(tolerance)+")"
                else:
                    np.testing.assert_allclose(extract, truth, rtol=tolerance)
                    per_key_metrics[key]["scoring_method"] = "float | reltol("+str(tolerance)+")"
                return True
            except AssertionError:
                return False

        elif type(truth) is not type(extract):
            per_key_metrics[key]["scoring_method"] = "type check"
            return False
        elif isinstance(truth, str):
            per_key_metrics[key]["scoring_method"] = "exact match"
            return truth.lower() == extract.lower()

        per_key_metrics[key]["scoring_method"] = "exact match"
        return truth == extract

    def _is_key_extractable(
        self, key: str, flat_extraction: Dict, flat_truth: Dict
    ) -> bool:
        """Check if a key is present and extractable."""
        return key in flat_extraction.keys() and not (
            flat_extraction[key] is None and flat_truth[key] is not None
        )

    def _is_key_judgable(
        self, key: str, flat_truth: Dict, flat_extraction: Dict
    ) -> bool:
        """Check if a key can be judged by LLM (both are non-empty strings)."""
        return (
            isinstance(flat_extraction[key], str)
            and isinstance(flat_truth[key], str)
            and len(flat_truth[key]) > 0
            and len(flat_extraction[key]) > 0
        )

    def _should_skip_key(self, key: str, tolerances: Dict[str, float]) -> bool:
        """Check if a key should be skipped during evaluation."""
        return key in tolerances or key == "additional_notes" or key.endswith(
            ":additional_notes"
        )

    def _regularize_repeated_key(self, key: str) -> str:
        """Remove digits from flattened keys to treat repeated elements as same type."""
        return "".join(char for char in key if not char.isdigit())

    def _update_precision_metrics(
        self, key: str, is_correct: bool, per_key_metrics: Dict[str, Dict[str, float]]
    ) -> None:
        """Update precision metrics for a key."""
        if is_correct:
            per_key_metrics[key]["TP"] += 1
        else:
            per_key_metrics[key]["FP"] += 1

    def _log_precision_mismatch(
        self, key: str, truth_value: Any, extraction_value: Any, match: Match
    ) -> None:
        """Log precision mismatches for debugging."""
        if "layers" in key:
            layer_names = [layer.get("name") for layer in match["truth"]["layers"]]
            print(f"Stack: {layer_names}")

        print(
            f"Mismatch for {key}: truth='{truth_value}' vs extraction='{extraction_value}'"
        )


def score_multiple_extractions(
    truth_extraction_pairs: List[Tuple[Dict, Dict, str]],
) -> Tuple[List[Evaluations], Dict[str, Dict]]:
    """
    Score multiple truth-extraction pairs.
    """
    evaluations = []
    per_key_metrics = {}

    for truth, extraction, filename in truth_extraction_pairs:
        print(f"Processing file: {filename}")

        file_metrics = defaultdict(lambda: defaultdict(float))
        evaluation = Evaluations(truth, extraction, filename, file_metrics)
        evaluations.append(evaluation)

        per_key_metrics[filename] = file_metrics

    total_missing_devices = sum(
        max(eval.devices_in_truth - eval.devices_found, 0) for eval in evaluations
    )
    total_devices = sum(eval.devices_in_truth for eval in evaluations)

    print(f"Total missing devices: {total_missing_devices} out of {total_devices}")

    return evaluations, per_key_metrics


def calculate_precision(per_key_metrics: Dict, key: str) -> Optional[float]:
    """Calculate precision for a given key."""
    tp = per_key_metrics[key]["TP"]
    fp = per_key_metrics[key]["FP"]

    if tp + fp > 0:
        return tp / (tp + fp)
    return None


def calculate_micro_metrics(per_key_metrics: Dict) -> Dict[str, float]:
    """Aggregate fact-level counts across every paper and schema key.

    This avoids a benchmark score changing merely because the same devices are
    grouped into more or fewer papers.
    """
    totals = {"TP": 0.0, "FP": 0.0, "FN": 0.0}
    for paper_metrics in per_key_metrics.values():
        for metrics in paper_metrics.values():
            for key in totals:
                totals[key] += float(metrics.get(key, 0.0))

    precision_denominator = totals["TP"] + totals["FP"]
    recall_denominator = totals["TP"] + totals["FN"]
    precision = totals["TP"] / precision_denominator if precision_denominator else 0.0
    recall = totals["TP"] / recall_denominator if recall_denominator else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {**totals, "precision": precision, "recall": recall, "f1": f1}
