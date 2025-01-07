from typing import Any, List, TypedDict
from copy import deepcopy
import warnings

from deepdiff import DeepDiff
from munkres import Munkres
import numpy as np
from Levenshtein import distance



class Matches(TypedDict):
    truth: dict
    extraction: dict


class Evaluations:
    """
    A utility class to evaluate the similarity between two structured datasets
    (e.g., JSON-like dictionaries) and compute a score ranging from 0 to 1,
    where 1 indicates the highest similarity.

    This class uses the DeepDiff library to compute the "deep distance" between
    two datasets, and the Munkres algorithm for optimal cell matching.

    Attributes:
        score (float): A similarity score between the datasets, where 1
            represents identical datasets.
        score_device_stacks (List[float]):
        score_device_layers (List[float]):
        precision_tolerances (dict):
        precisions_average (dict):
        devices_found (int):
        recall_devices (float):
        devices_matched (int):
        matches (List[Matches]): A list of mappings between truth and extraction
            cells after optimal matching.
        detailed_score (dict):

    Args:
        truth (dict): The reference dataset (ground truth).
        extraction (dict): The dataset to evaluate against the truth.
    """

    score: float = None
    score_device_stacks: List[float] = None
    score_device_layers: List[float] = None
    precision_tolerances: dict = {
        "pce": 0.1,
        "jsc": 0.1,
        "voc": 0.01,
        "ff": 0.1,
        "device_stack": 0.80,
    }
    precisions_average: dict = None
    devices_found: int = 0
    recall_devices: float = None
    devices_matched: int = None
    matches: List[Matches] = None
    detailed_score: dict = None

    def __init__(self, truth: dict, extraction: dict):
        self.score = inverted_deepdiff(truth["cells"], extraction["cells"])
        self.matches = match_cells(truth["cells"], extraction["cells"])
        self.devices_found = len(extraction["cells"])
        self.recall_devices = min(self.devices_found / len(truth["cells"]), 1)
        self.devices_matched = len(self.matches)
        self.score_device_stacks = score_device_stacks(self.matches)
        self.score_device_layers = score_device_layers(self.matches)
        self.score_precisions = score_precisions(
            self.matches, self.precision_tolerances
        )
        self.precisions_average = float(np.mean(self.score_precisions))
        self.detailed_score = score_cells_detailed(self.matches)


def score_cells_detailed(matches: List[Matches]) -> dict:
    maes = {"pce": [], "ff": [], "voc": [], "jsc": []}  # TODO: stability
    perovskite_composition_distances = []
    for match in matches:
        for key in maes:
            maes[key].append(
                abs(
                    safe_get_value(match["truth"], key)
                    - safe_get_value(match["extraction"], key)
                )
            )
        perovskite_composition_distances.append(
            distance(
                match["truth"]["perovskite_composition"],
                match["extraction"]["perovskite_composition"],
            )
        )

    for key in maes:
        maes[key] = float(np.mean(maes[key]))

    return {
        "MAEs": maes,
        "perovskite_composition_distances": perovskite_composition_distances,
    }


def score_precisions(matches: List[Matches], precision_tolerances: dict):
    """Gets the overall precision for all keys listed in precision_tolerances for every cell"""
    precisions = []
    for match in matches:
        found = []
        for key, tolerance in precision_tolerances.items():
            found.append(
                inverted_deepdiff(
                    safe_get_value(match["truth"], key),
                    safe_get_value(match["extraction"], key),
                )
                > precision_tolerances[key]
            )
        precisions.append(sum(found) / len(found))
    return precisions


def score_device_layers(matches: List[Matches]) -> List[float]:
    scores = []
    for match in matches:
        scores.append(
            inverted_deepdiff(
                match["truth"].get("layers", []), match["extraction"].get("layers", [])
            )
        )
    return scores


def score_device_stacks(matches: List[Matches]) -> List[float]:
    scores = []
    for match in matches:
        scores.append(
            inverted_deepdiff(
                "".join(
                    [
                        layer.get("name", "")
                        for layer in match["truth"].get("layers") or []
                    ]
                ),
                "".join(
                    [
                        layer.get("name", "")
                        for layer in match["extraction"].get("layers") or []
                    ]
                ),
            )
        )
    return scores


def inverted_deepdiff(lhs: Any, rhs: Any) -> float:
    """Compute a score ranging from 0 to 1, where 1 indicates the highest similarity"""
    return (
        1
        - DeepDiff(lhs, rhs, get_deep_distance=True, ignore_string_case=True)[
            "deep_distance"
        ]
    )


def safe_get_value(d: Any, key: str):
    try:
        d[key]
    except KeyError:
        warnings.warn(
            f"Couldn't evaluate {key} because it is missing in one of the cells."
        )
        return 0
    try:
        return d[key]["value"]
    except TypeError:
        return d[key]


def match_cells(truth_cells: List[dict], extracted_cells: List[dict]) -> List[dict]:
    """Matches cells from the truth and extraction and stores them in a new object called matches."""
    m = Munkres()

    truth_stacks = [
        "".join([layer.get("name", "") for layer in t.get("layers") or []])
        for t in truth_cells
    ]
    extracted_stacks = [
        "".join([layer.get("name", "") for layer in e.get("layers") or []])
        for e in extracted_cells
    ]
    truth_depositions = [
        [layer.get("deposition") for layer in t.get("layers") or []]
        for t in truth_cells
    ]
    extracted_depositions = [
        [layer.get("deposition") for layer in e.get("layers") or []]
        for e in extracted_cells
    ]

    # rows = truth, cols = extraction
    scores = [
        [
            (0.7 * -inverted_deepdiff(truth_stacks[tid], extracted_stacks[eid]))
            + (
                0.2
                * -inverted_deepdiff(truth_depositions[tid], extracted_depositions[eid])
            )
            + (0.1 * -inverted_deepdiff(t, e))
            for eid, e in enumerate(extracted_cells)
        ]
        for tid, t in enumerate(truth_cells)
    ]
    indexes = m.compute(scores)

    matches = [
        {
            "truth": deepcopy(truth_cells[row]),
            "extraction": deepcopy(extracted_cells[col]),
        }
        for row, col in indexes
    ]
    return matches
