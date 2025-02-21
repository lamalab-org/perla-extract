from typing import Any, List, TypedDict
from copy import deepcopy
import warnings

from deepdiff import DeepDiff
from munkres import Munkres
import numpy as np
from Levenshtein import distance, ratio
import flatdict
from perovscribe.postprocessing import complete_solar_cell_dict
from perovscribe.llm_call import llm_as_judge

from collections import defaultdict

per_key_metrics = defaultdict(dict)


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

    Note: The input datasets have to be normalized with perovscribe.postprocessing.

    Attributes:
        score (float): A similarity score between the datasets, where 1
            represents identical datasets.
        score_device_stacks (List[float]):
        score_device_layers (List[float]):
        precision_tolerances (dict):
        precisions_average (dict):
        devices_in_truth (int):
        devices_found (int):
        recall_devices (float):
        devices_matched (int):
        matches (List[Matches]): A list of mappings between truth and extraction
            cells after optimal matching.
        detailed_score (dict):

    Args:
        truth (dict): The reference dataset (ground truth).
        extraction (dict): The dataset to evaluate against the truth.
        file (str): Name of the extraction file being checked.
    """

    score: float = None
    score_device_stacks: List[float] = None
    score_device_layers: List[float] = None
    precision_tolerances: dict = {
        "pce": 0.1,
        "jsc": 0.1,
        "voc": 0.01,
        "ff": 0.1,
        "active_area": 0.01,
    }
    precisions_average: dict = None
    devices_in_truth: int = 0
    devices_found: int = 0
    recall_devices: float = None
    devices_matched: int = None
    matches: List[Matches] = None
    detailed_score: dict = None

    def __init__(self, truth: dict, extraction: dict, file: str):
        self.score = inverted_deepdiff(truth["cells"], extraction["cells"])
        self.matches = match_cells(truth["cells"], extraction["cells"], file)
        self.devices_in_truth = len(truth["cells"])
        self.devices_found = len(extraction["cells"])
        self.recall_devices = min(self.devices_found / len(truth["cells"]), 1)
        self.devices_matched = len(self.matches)
        self.score_device_stacks = score_device_stacks(
            self.matches
        )  # These two should be the same
        self.score_device_layers = score_device_layers(
            self.matches
        )  # These two. maybe replace with str_similarity too?
        self.score_precisions, self.llm_judge_calls = score_precisions(
            self.matches, self.precision_tolerances
        )
        self.precisions_average = float(np.mean(self.score_precisions))
        self.detailed_score = score_cells_detailed(self.matches)
        self.score_recalls = score_recalls(self.matches) + [0] * max(
            0, self.devices_in_truth - self.devices_found
        )
        self.recalls_average = float(np.mean(self.score_recalls))


def score_multiple_extractions(truth_extraction_pairs: tuple[dict, dict, str]):
    global per_key_metrics
    evals = []

    for truth, extraction, file in truth_extraction_pairs:
        evals.append(Evaluations(truth, extraction, file))
    print(
        "Total missing devices",
        sum([max(eval.devices_in_truth - eval.devices_found, 0) for eval in evals]),
    )
    return evals, per_key_metrics


# TODO: Replace the safe_get_value function below
def score_cells_detailed(matches: List[Matches]) -> dict:
    maes = {"pce": [], "ff": [], "voc": [], "jsc": []}  # TODO: stability
    perovskite_composition_similarity = []
    for match in matches:
        for key in maes:
            if safe_get_value(match["truth"], key) is not None:
                maes[key].append(
                    abs(
                        safe_get_value(match["truth"], key)
                        - (safe_get_value(match["extraction"], key) or 0.0)
                    )
                )
            elif (
                safe_get_value(match["extraction"], key) is not None
                and safe_get_value(match["truth"], key) is None
            ):
                print("================ HALLUCINATION =================")
                print(
                    "For cell with layers: ",
                    " ".join(
                        [
                            layer.get("name", "")
                            for layer in match["extraction"].get("layers")
                        ]
                    ),
                )
                print(
                    "Value hallucinated:", key, safe_get_value(match["extraction"], key)
                )
        if (
            match["extraction"].get("perovskite_composition") is not None
            and match["extraction"].get("perovskite_composition").get("formula", None)
            is not None
        ):
            perovskite_composition_similarity.append(
                str_similarity(
                    [match["truth"]["perovskite_composition"]["formula"]],
                    [match["extraction"]["perovskite_composition"]["formula"]],
                )
            )

    for key in maes:
        maes[key] = float(np.mean(maes[key]))

    return {
        "MAEs": maes,
        "perovskite_composition_similarity": perovskite_composition_similarity,
    }


def score_recalls(matches: List[Matches]) -> List[float]:
    """Recalls"""
    global per_key_metrics
    recalls = []
    for match in matches:
        found = []

        match["truth"]["layers"], match["extraction"]["layers"] = match_layers(
            match["truth"]["layers"], match["extraction"]["layers"]
        )

        flat_truth = flatdict.FlatterDict(
            complete_solar_cell_dict(match["truth"])
        )  # TODO: Run normalize after complete_solar_cell dict. Add loop in the complete.. func for all cells
        flat_extraction = flatdict.FlatterDict(match["extraction"])

        for key in flat_truth.keys():
            key_for_stats = "".join([i for i in key if not i.isdigit()])

            if key not in flat_extraction.keys() or (
                key in flat_extraction.keys()
                and flat_extraction[key] is None
                and flat_truth[key] is not None
            ):
                found.append(False)
            else:
                found.append(True)

            if "FN" not in per_key_metrics[key_for_stats]:
                per_key_metrics[key_for_stats]["FN"] = 0
            if not found[-1]:
                per_key_metrics[key_for_stats]["FN"] += 1
        recalls.append(sum(found) / len(found))
    return recalls


def match_layers(truth: List[dict], extraction: List[dict]):
    """Just match layer elements within a match"""
    scores = []

    # rows = truth, cols = extraction
    scores = [
        [
            distance(
                t.get("functionality", "") + t.get("name", ""),
                e.get("functionality", "") + e.get("name", ""),
            )
            for eid, e in enumerate(extraction)
            if len(extraction) != 0
        ]
        for tid, t in enumerate(truth)
    ]

    m = Munkres()

    indexes = m.compute(scores)

    return [truth[row] for row, col in indexes], [
        extraction[col] for row, col in indexes
    ]


def score_precisions(matches: List[Matches], precision_tolerances: dict) -> List[float]:
    """Gets the overall precision for all keys listed in precision_tolerances for every cell"""
    global per_key_metrics
    precisions = []
    llm_judge_calls = 0
    for match in matches:
        found = []

        match["truth"]["layers"], match["extraction"]["layers"] = match_layers(
            match["truth"]["layers"], match["extraction"]["layers"]
        )

        def is_key_judgable(key) -> bool:
            return (
                isinstance(flat_extraction[key], str)
                and isinstance(flat_truth[key], str)
                and flat_truth[key].lower() != flat_extraction[key].lower()
                and (
                    key in ("perovskite_composition:formula", "light_source:lamp")
                    or key.split(":")[0] in ("device_stack", "layers")
                )
            )

        flat_truth = flatdict.FlatterDict(complete_solar_cell_dict(match["truth"]))
        flat_extraction = flatdict.FlatterDict(match["extraction"])

        for key, tolerance in precision_tolerances.items():
            key = key + ":value"
            if "TP" not in per_key_metrics[key]:
                per_key_metrics[key]["TP"] = 0
            if "FP" not in per_key_metrics[key]:
                per_key_metrics[key]["FP"] = 0
            if key in flat_extraction.keys() and (
                flat_extraction[key] is not None or flat_truth[key] is None
            ):
                found.append(
                    abs((flat_truth[key] or 999.0) - (flat_extraction[key] or 0.0))
                    < tolerance
                )
                if found[-1]:
                    per_key_metrics[key]["TP"] += 1
                else:
                    per_key_metrics[key]["FP"] += 1

        for key in flat_truth:
            key_for_stats = "".join([i for i in key if not i.isdigit()])
            if "TP" not in per_key_metrics[key_for_stats]:
                per_key_metrics[key_for_stats]["TP"] = 0
            if "FP" not in per_key_metrics[key_for_stats]:
                per_key_metrics[key_for_stats]["FP"] = 0

            if flat_extraction is None or key in [
                x + ":value" for x in precision_tolerances.keys()
            ]:
                continue

            if key in flat_extraction.keys() and (
                flat_extraction[key] is not None or flat_truth[key] is None
            ):
                found.append(
                    flat_truth[key] == flat_extraction[key]
                    if type(flat_truth[key]) is type(flat_extraction[key])
                    else False
                )
                if not found[-1] and is_key_judgable(key):
                    judgement = llm_as_judge(
                        match["truth"], flat_truth[key], flat_extraction[key]
                    )
                    llm_judge_calls += 1
                    print(
                        "For:",
                        key,
                        flat_truth[key],
                        flat_extraction[key],
                        "LLM says",
                        judgement.judgement,
                    )
                    found[-1] = judgement.judgement

                if found[-1]:
                    per_key_metrics[key_for_stats]["TP"] += 1
                else:
                    per_key_metrics[key_for_stats]["FP"] += 1

        precisions.append(sum(found) / len(found))
    return precisions, llm_judge_calls


def score_device_layers(matches: List[Matches]) -> List[float]:
    scores = []
    for match in matches:
        scores.append(
            inverted_deepdiff(
                match["truth"]["layers"], match["extraction"].get("layers", [])
            )
        )
    return scores


def score_device_stacks(matches: List[Matches]) -> List[float]:
    scores = []
    for match in matches:
        scores.append(
            inverted_deepdiff(
                " ".join(
                    [
                        layer.get("name", "")
                        for layer in match["truth"].get("layers") or []
                    ]
                ),
                " ".join(
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
        - DeepDiff(
            lhs,
            rhs,
            get_deep_distance=True,
            ignore_string_case=True,
            ignore_string_type_changes=True,
            ignore_numeric_type_changes=True,
            significant_digits=2,
            number_format_notation="e",
            cutoff_intersection_for_pairs=1,
        )["deep_distance"]
    )


def safe_get_value(d: Any, key: str):
    try:
        d[key]
    except KeyError:
        warnings.warn(
            f"Couldn't evaluate {key} because it is missing in one of the cells."
        )
        return np.nan
    try:
        return d[key]["value"]
    except TypeError:
        return d[key]


def str_similarity(s1, s2) -> float:
    # Just remove SLG
    try:
        s1.remove("SLG")
        s2.remove("SLG")
    except ValueError:
        pass

    disses = []

    for id, s1ss in enumerate(s1):
        if id < len(s2):
            if " " in s1[id] or " " in s2[id]:
                disses.append(str_similarity(s1[id].split(" "), s2[id].split(" ")))
                print(disses[-1], s1[id].split(" "), s2[id].split(" "))
            else:
                disses.append(ratio(s1[id], s2[id]))
    return np.mean(disses)


def match_cells(
    truth_cells: List[dict], extracted_cells: List[dict], file
) -> List[dict]:
    """Matches cells from the truth and extraction and stores them in a new object called matches."""
    m = Munkres()

    truth_stacks = [
        [layer.get("name", "") for layer in t.get("layers") or []] for t in truth_cells
    ]
    extracted_stacks = [
        [layer.get("name", "") for layer in e.get("layers") or []]
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
            (0.7 * -str_similarity(truth_stacks[tid], extracted_stacks[eid]))
            + (
                0.2
                * -inverted_deepdiff(truth_depositions[tid], extracted_depositions[eid])
            )
            + (0.1 * -inverted_deepdiff(t, e))
            for eid, e in enumerate(extracted_cells)
            if len(extracted_stacks[eid]) != 0
        ]
        for tid, t in enumerate(truth_cells)
    ]

    for tid, t in enumerate(truth_cells):
        for eid, e in enumerate(extracted_cells):
            print("-------")
            print(file)
            print(truth_stacks[tid], "--", extracted_stacks[eid])
            print(-str_similarity(truth_stacks[tid], extracted_stacks[eid]))
            print(
                -str_similarity(
                    [layer.get("name", "") for layer in t.get("layers") or []],
                    [layer.get("name", "") for layer in e.get("layers") or []],
                )
            )

    indexes = m.compute(scores)

    matches = [
        {
            "truth": deepcopy(truth_cells[row]),
            "extraction": deepcopy(extracted_cells[col]),
        }
        for row, col in indexes
    ]

    print("===================================================")
    for match in matches:
        print("------------------------------------------------")
        print(file)
        print(
            " ".join([layer.get("name", "") for layer in match["truth"].get("layers")])
        )
        if match["extraction"].get("layers") is not None:
            print(
                " ".join(
                    [
                        layer.get("name", "")
                        for layer in match["extraction"].get("layers")
                    ]
                )
            )

    return matches
