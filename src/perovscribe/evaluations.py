from typing import List
from copy import deepcopy

from deepdiff import DeepDiff
from munkres import Munkres


class Evaluations:
    """
    A utility class to evaluate the similarity between two structured datasets
    (e.g., JSON-like dictionaries) and compute a score ranging from 0 to 1,
    where 1 indicates the highest similarity.

    This class uses the DeepDiff library to compute the "deep distance" between
    two datasets, and the Munkres algorithm for optimal cell matching.

    Attributes:
        deep_results (dict): The results of the DeepDiff comparison, including
            details about differences and the computed deep distance.
        score (float): A similarity score between the datasets, where 1
            represents identical datasets.
        matches (List[dict]): A list of mappings between truth and extraction
            cells after optimal matching.

    Args:
        truth (dict): The reference dataset (ground truth).
        extraction (dict): The dataset to evaluate against the truth.
    """

    deep_results: dict = None
    score: float = None
    matches: List[dict] = None

    def __init__(self, truth: dict, extraction: dict):
        self.truth = truth
        self.extraction = extraction
        self.deep_results = DeepDiff(
            truth,
            extraction,
            ignore_order=True,
            get_deep_distance=True,
            significant_digits=1,
        )
        self.score = (
            1 - self.deep_results["deep_distance"]
            if "deep_distance" in self.deep_results
            else 1
        )

    def match(self) -> matches:
        """Matches cells from the truth and extraction and stores them in a new object called matches."""
        m = Munkres()

        # rows = truth, cols = extraction
        scores = [
            [
                1 - Evaluations(t["layers"], e["layers"]).score
                for e in self.extraction["cells"]
            ]
            for t in self.truth["cells"]
        ]
        indexes = m.compute(scores)
        print(indexes)

        self.matches = [
            {
                "truth": self.truth["cells"][row],
                "extraction": self.extraction["cells"][col],
            }
            for row, col in indexes
        ]

        return deepcopy(self.matches)
