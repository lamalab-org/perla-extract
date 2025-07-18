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
        per_key_metrics (dict[dict[float]]): An external variable to store important metrics per extraction key.
    """

    score: float = None
    score_device_stacks: List[float] = None
    score_device_layers: List[float] = None
    precision_tolerances: dict = {
        "pce": 0.1,  # 0.1 abs
        "jsc": 0.1,  # 0.1 abs
        "voc": 0.01,  # 0.01 abs
        "ff": 0.1,  # 0.1 abs
    }
    precisions_average: dict = None
    devices_in_truth: int = 0
    devices_found: int = 0
    recall_devices: float = None
    devices_matched: int = None
    matches: List[Matches] = None
    detailed_score: dict = None

    def __init__(
        self,
        truth: dict,
        extraction: dict,
        file: str,
        per_key_metrics: dict[dict[float]],
    ):
        self.devices_in_truth = len(truth["cells"])
        self.devices_found = len(extraction["cells"])

        # Add missing empty devices for FN
        if self.devices_in_truth > self.devices_found:
            extraction["cells"] += [
                {"layers": []}
                for i in range(self.devices_in_truth - self.devices_found)
            ]

        # Add missing FN for sections I match. Like layers and deposition. This should be done before matching.
        # no_layers_to_add = 0
        # no_depositions_to_add = 0
        # no_of_truth_layers = 0
        # no_of_truth_depositions = 0
        # no_of_extract_layers = 0
        # no_of_extract_depositions = 0
        # for cell in truth["cells"]:
        #     no_of_truth_layers += len(cell["layers"])
        #     for layer in cell["layers"]:
        #         no_of_truth_depositions += len(layer["deposition"] or [])
        # for cell in extraction["cells"]:
        #     no_of_extract_layers += len(cell["layers"])
        #     for layer in cell["layers"]:
        #         no_of_extract_depositions += len(layer["deposition"] or [])
        # # for match in self.matches:
        # #     no_layers_to_add += max(len(match["truth"]["layers"]) - len(match["extraction"]["layers"]), 0)
        # #     no_truth_depositions = 0
        # #     no_extract_depositions = 0
        # #     for layer in match["truth"]["layers"]:
        # #         no_truth_depositions += len(layer["deposition"] or [])
        # #     for layer in match["extraction"]["layers"]:
        # #         no_extract_depositions += len(layer["deposition"] or [])
        # #     no_depositions_to_add += max(no_truth_depositions - no_extract_depositions, 0)

        self.score = inverted_deepdiff(truth["cells"], extraction["cells"])
        self.matches = match_cells(truth["cells"], extraction["cells"], file)
        self.devices_matched = len(self.matches)
        self.recall_devices = min(self.devices_matched / len(truth["cells"]), 1)
        self.score_device_stacks = score_device_stacks(
            self.matches
        )  # These two should be the same
        self.score_device_layers = score_device_layers(
            self.matches
        )  # These two. maybe replace with str_similarity too?
        self.score_precisions, self.llm_judge_calls = score_precisions(
            self.matches, self.precision_tolerances, per_key_metrics
        )
        self.precisions_average = float(np.mean(self.score_precisions))
        self.score_recalls = pad_missing_devices(
            score_recalls(self.matches, per_key_metrics),
            self.devices_in_truth,
            self.devices_found,
        )
        self.recalls_average = float(np.mean(self.score_recalls))

        # Add FN for missing devices - this also adds the numbers calculated above for matched sections
        # for key in per_key_metrics:
        #     per_key_metrics[key]["FN"] += max(self.devices_in_truth - self.devices_found, 0)
        #     if "layers::deposition" in key:
        #         per_key_metrics[key]["FN"] += max(no_of_truth_depositions-no_of_extract_depositions, 0)
        #     elif "layers" in key:
        #         per_key_metrics[key]["FN"] += max(no_of_truth_layers-no_of_extract_layers, 0)


def pad_missing_devices(
    score: List[float], devices_in_truth, devices_found
) -> List[float]:
    """Adds as many 0's as there are devices missing in the extraction."""
    return score + [0] * max(0, devices_in_truth - devices_found)


# Remove
def calc_precision(per_key_metrics, key):
    if per_key_metrics[key]["TP"] + per_key_metrics[key]["FP"] > 0:
        return per_key_metrics[key]["TP"] / (
            per_key_metrics[key]["TP"] + per_key_metrics[key]["FP"]
        )
    return None


def score_multiple_extractions(truth_extraction_pairs: tuple[dict, dict, str]):
    evals = []
    per_key_metrics = defaultdict(lambda: defaultdict(float))

    for truth, extraction, file in truth_extraction_pairs:
        layer_scores = []
        print("File:", file)
        paper_per_key_metrics = defaultdict(lambda: defaultdict(float))
        evals.append(Evaluations(truth, extraction, file, paper_per_key_metrics))
        layers_keys = [key for key in paper_per_key_metrics if "layers" in key.lower()]

        for key in layers_keys:
            layer_scores.append(calc_precision(paper_per_key_metrics, key))
        per_key_metrics[file] = paper_per_key_metrics

    total_missing_devices = sum(
        [max(eval.devices_in_truth - eval.devices_found, 0) for eval in evals]
    )
    print(
        "Total missing devices",
        total_missing_devices,
        sum(eval.devices_in_truth for eval in evals),
    )

    return evals, per_key_metrics


def regularize_repeated_key(key):
    """
    When using FlatDict, children of lists get digits in the flat_key. We remove this digits to make the keys treated as the same.
    For example: layers:1:name -> layers::name, layers:0:name -> layers::name
    """
    return "".join([i for i in key if not i.isdigit()])


def score_recalls(
    matches: List[Matches], per_key_metrics: dict[dict[float]]
) -> List[float]:
    """Recalls"""
    recalls = []
    for match in matches:
        found = []
        # TODO: Use the group implementation and add NOTFOUND for layers that don't get matched on either side.
        match["truth"]["layers"], match["extraction"]["layers"] = match_layers(
            match["truth"].get("layers", []), match["extraction"].get("layers", [])
        )

        flat_truth = flatdict.FlatterDict(
            complete_solar_cell_dict(match["truth"])
        )  # TODO: Run normalize after complete_solar_cell dict. Add loop in the complete.. func for all cells
        flat_extraction = flatdict.FlatterDict(match["extraction"])

        for key in flat_truth.keys():  # TODO: Make sure you mention that you loop over the flattened dict in the paper.
            if key == "additional_notes":
                continue

            key_for_stats = regularize_repeated_key(key)

            if key not in flat_extraction.keys() or (
                key in flat_extraction.keys()
                and flat_extraction[key] is None
                and flat_truth[key] is not None
            ):
                found.append(False)
            else:
                found.append(True)

            if not found[-1]:
                per_key_metrics[key_for_stats]["FN"] += 1
                if "active_area" in key_for_stats:
                    print(
                        per_key_metrics[key_for_stats]["FN"],
                        "adding FN for",
                        flat_truth[key],
                        key in flat_extraction.keys(),
                        (
                            key not in flat_extraction.keys()
                            and flat_truth[key] is not None
                        ),
                    )
        recalls.append(sum(found) / len(found))
    return recalls


def match_depositions(truth: List[dict], extraction: List[dict]):
    """Just match deposition elements within layers"""
    scores = []

    if (
        truth is None or len(truth) == 0
    ):  # NOTE: I had to do a lot of .get("layers", []) or [] type stuff. dunno if it breaks something like crazy.
        truth = []

    if extraction is None:
        extraction = []

    if len(truth) > len(extraction):
        extraction += [{} for i in range(len(truth) - len(extraction))]

    # rows = truth, cols = extraction
    scores = [
        [
            distance(
                (t.get("method", "NOTRUTH") or "NOTRUTH")
                + str(((t.get("temperature", {}) or {}).get("value") or "NOEXTRACT")),
                (e.get("method", "NOEXTRACT") or "NOEXTRACT")
                + str(((e.get("temperature", {}) or {}).get("value") or "NOEXTRACT")),
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


def match_layers(truth: List[dict], extraction: List[dict]):
    """Just match layer elements within a match"""
    scores = []

    if (
        truth is None or len(truth) == 0
    ):  # NOTE: I had to do a lot of .get("layers", []) or [] type stuff. dunno if it breaks something like crazy.
        truth = [{}]

    if extraction is None:
        extraction = [{}]

    if len(truth) > len(extraction):
        extraction += [
            {
                "deposition": None,
            }
            for i in range(len(truth) - len(extraction))
        ]

    # rows = truth, cols = extraction
    scores = [
        [
            distance(
                (t.get("functionality", "NOTRUTH") or "NOTRUTH")
                + (t.get("name", "NOTRUTH") or "NOTRUTH"),
                (e.get("functionality", "NOEXTRACT") or "NOEXTRACT")
                + (e.get("name", "NOEXTRACT") or "NOEXTRACT"),
            )
            for eid, e in enumerate(extraction)
            if len(extraction) != 0
        ]
        for tid, t in enumerate(truth)
    ]

    m = Munkres()

    indexes = m.compute(scores)

    for row, col in indexes:
        truth[row]["deposition"], extraction[col]["deposition"] = match_depositions(
            truth[row].get("deposition") or [{}],
            extraction[col].get("deposition") or [{}],
        )

    return [truth[row] for row, col in indexes], [
        extraction[col] for row, col in indexes
    ]


def is_within_rel_tolerance(truth, extract, rtol, abs=False) -> bool:
    try:
        if abs:
            np.testing.assert_allclose(extract, truth, atol=rtol)
        np.testing.assert_allclose(
            extract, truth, rtol=rtol
        )  # We flip extract and truth to make sure we have rtol applied on truth
    except AssertionError:
        return False
    return True


def is_value_correct(truth, extract, rtol=0.01, abs=False) -> bool:
    if isinstance(truth, int):
        truth = float(truth)
    if isinstance(extract, int):
        extract = float(extract)

    def are_equal_lower_strings(truth, extract) -> bool:
        return (
            isinstance(truth, str)
            and isinstance(extract, str)
            and truth.lower() == extract.lower()
        )

    if type(truth) is not type(extract):
        return False
    elif isinstance(truth, (int, float)):
        return is_within_rel_tolerance(truth, extract, rtol, abs)
    elif isinstance(truth, str):
        return are_equal_lower_strings(truth, extract)
    return truth == extract


def score_precisions(
    matches: List[Matches],
    precision_tolerances: dict,
    per_key_metrics: dict[dict[float]],
) -> List[float]:
    """Gets the overall precision for all keys listed in precision_tolerances for every cell"""

    def fix_precision_tolerances_keys(precision_tolerances: dict) -> dict:
        """Adds the string 'value' to all keys in precision_tolerances to access the values in the actual data dicts."""
        fixed_tolerances = {}
        for key in precision_tolerances.keys():
            fixed_tolerances[key + ":value"] = precision_tolerances[key]
        return fixed_tolerances

    precision_tolerances = fix_precision_tolerances_keys(precision_tolerances)

    precisions = []
    llm_judge_calls = 0
    for match in matches:
        found = []

        # TODO: Use the group implementation and add NOTFOUND for layers that don't get matched on either side.
        match["truth"]["layers"], match["extraction"]["layers"] = match_layers(
            match["truth"].get("layers", []), match["extraction"].get("layers", [])
        )

        def is_key_judgable(key, flat_truth, flat_extraction) -> bool:
            return (
                isinstance(flat_extraction[key], str)
                and isinstance(flat_truth[key], str)
                and len(flat_truth[key]) > 0
                and len(flat_extraction[key]) > 0
            )

        flat_truth = flatdict.FlatterDict(complete_solar_cell_dict(match["truth"]))
        flat_extraction = flatdict.FlatterDict(match["extraction"])

        for key, tolerance in precision_tolerances.items():
            if key in flat_extraction.keys() and (
                flat_extraction[key] is not None or flat_truth[key] is None
            ):
                found.append(
                    is_value_correct(
                        flat_truth[key], flat_extraction[key], tolerance, abs=True
                    )
                )
                # Checks if the last element found was accepted as a positive value and increments the True Positive count.
                if found[-1]:
                    per_key_metrics[key]["TP"] += 1
                # If the last element was not found to be acceptable, the False Positive count is incremented.
                else:
                    # print(
                    #     "stack",
                    #     [layer.get("name") for layer in match["truth"]["layers"]],
                    # )
                    # print(
                    #     "We have",
                    #     flat_truth[key],
                    #     "in the ground truth and",
                    #     flat_extraction[key],
                    #     "in the extraction for",
                    #     key,
                    # )
                    per_key_metrics[key]["FP"] += 1

        for key in flat_truth:
            key_for_stats = regularize_repeated_key(key)

            if (
                flat_extraction is None
                or key in precision_tolerances.keys()
                or key == "additional_notes"
            ):
                continue

            if key in flat_extraction.keys() and (
                flat_extraction[key] is not None or flat_truth[key] is None
            ):
                found.append(is_value_correct(flat_truth[key], flat_extraction[key]))
                if not found[-1] and is_key_judgable(key, flat_truth, flat_extraction):
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

                # Checks if the last element found was accepted as a positive value and increments the True Positive count.
                if found[-1]:
                    per_key_metrics[key_for_stats]["TP"] += 1
                    if "active_area" in key:
                        print(
                            per_key_metrics[key_for_stats]["TP"],
                            "adding TP for",
                            flat_truth[key],
                            flat_extraction[key],
                        )
                # If the last element was not found to be acceptable, the False Positive count is incremented.
                else:
                    if "layers" in key:
                        print(
                            "stack",
                            [layer.get("name") for layer in match["truth"]["layers"]],
                        )
                    print(
                        "We have",
                        flat_truth[key],
                        "in the ground truth and",
                        flat_extraction[key],
                        "in the extraction for",
                        key,
                    )
                    if "active_area" in key:
                        print(
                            per_key_metrics[key_for_stats]["FP"],
                            "adding FP for",
                            flat_truth[key],
                            flat_extraction[key],
                        )
                    per_key_metrics[key_for_stats]["FP"] += 1

        precisions.append(sum(found) / max(len(found), 1))
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
                        layer.get("name", "NOTRUTH")
                        for layer in match["truth"].get("layers") or []
                    ]
                ),
                " ".join(
                    [
                        layer.get("name", "NOEXTRACT") or "NOEXTRACT"
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


def str_similarity(s1: List[str], s2: List[str]) -> float:
    """
    Computes the similarity between two lists of strings using the Levenshtein ratio.

    The function first removes occurrences of "SLG" from both lists (if present) by
    working with copies to avoid modifying the original inputs. Then, it iterates
    through the elements, comparing them using the Levenshtein ratio. If an element
    contains spaces, it is split into sub-strings and compared recursively.

    Args:
        s1 (List[str]): The first list of strings.
        s2 (List[str]): The second list of strings.

    Returns:
        float: The average similarity score between corresponding elements of s1 and s2.

    Notes:
        - Uses `Levenshtein.ratio()` to compute string similarity.
        - If "SLG" is present in `s1` or `s2`, it is removed before comparison.
        - Recursively processes elements containing spaces.
        - Uses `np.mean()` to compute the final similarity score.
        - Works with copies of `s1` and `s2` to avoid modifying the original lists.
    """
    s1 = s1.copy()
    s2 = s2.copy()
    # Just remove SLG
    try:
        s1.remove("SLG")
        s2.remove("SLG")
    except ValueError:
        pass

    similarity_ratios = 0

    for id, s1ss in enumerate(s1):
        if id < len(s2):
            if " " in s1[id] or " " in s2[id]:
                similarity_ratios += str_similarity(
                    s1[id].split(" "), s2[id].split(" ")
                )
            else:
                similarity_ratios += ratio(s1[id], s2[id])
    return similarity_ratios


def match_cells(
    truth_cells: List[dict], extracted_cells: List[dict], file
) -> List[dict]:
    """Matches cells from the truth and extraction and stores them in a new object called matches."""
    m = Munkres()

    truth_functionalites = []
    for t in truth_cells:
        functionalities = defaultdict(list)
        for layer in t.get("layers", []) or []:
            functionalities[layer.get("functionality")].append(
                layer.get("name", "incorrect") or "incorrect"
            )
        truth_functionalites.append(functionalities)

    extract_functionalites = []
    for e in extracted_cells:
        functionalities = defaultdict(list)
        for layer in e.get("layers", []) or []:
            functionalities[layer.get("functionality")].append(
                layer.get("name", "incorrect") or "incorrect"
            )
        extract_functionalites.append(functionalities)

    def score_functionalities(t_funcs, e_funcs) -> float:
        distance = 0
        for t_func in t_funcs:
            if t_func not in e_funcs:
                e_funcs[t_func].append("NOTFOUND")
            distance += -str_similarity(t_funcs[t_func], e_funcs[t_func])
        return distance

    stack_scores = [
        [
            score_functionalities(truth_functionality, extract_functionality)
            for extract_functionality in extract_functionalites
        ]
        for truth_functionality in truth_functionalites
    ]
    # for truth_functionality in truth_functionalites:
    #     for extract_functionality in extract_functionalites:
    #         print(
    #             "SCORES:",
    #             score_functionalities(truth_functionality, extract_functionality),
    #             truth_functionality,
    #             extract_functionality,
    #         )

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
            (0.7 * stack_scores[tid][eid])
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

    # print("===================================================")
    # for match in matches:
    #     print("------------------------------------------------")
    #     print(file)
    #     print(
    #         " ".join(
    #             [layer.get("name", "") for layer in match["truth"].get("layers", [])]
    #         )
    #     )
    #     if match["extraction"].get("layers") is not None:
    #         print(
    #             " ".join(
    #                 [
    #                     layer.get("name", "")
    #                     for layer in match["extraction"].get("layers")
    #                 ]
    #             )
    #         )

    return matches
