from typing import List, Tuple
from copy import deepcopy

from deepdiff import DeepDiff
from munkres import Munkres

class Evaluations:
    deep_results = None
    score = None
    matches = List[Tuple[int, int]]

    def __init__(self, truth: dict, extraction: dict):
        """Calculates a score between 0 and 1 using deepdiff. 1 being the best."""
        self.truth = truth
        self.extraction = extraction
        self.deep_results = DeepDiff(truth, extraction, ignore_order=True, get_deep_distance=True, significant_digits=1)
        self.score = 1-self.deep_results["deep_distance"] if "deep_distance" in self.deep_results else 1

    def match(self) -> matches:
        """Matches cells from the truth and extraction and stores them in a new object called matches."""
        m = Munkres()

        # rows = truth, cols = extraction
        scores = [[1 - Evaluations(t["layers"], e["layers"]).score for e in self.extraction["cells"]] for t in self.truth["cells"]]
        indexes = m.compute(scores)
        print(indexes)

        self.matches = [{"truth": self.truth["cells"][row], "extraction": self.extraction["cells"][col]} for row, col in indexes]

        return deepcopy(self.matches)