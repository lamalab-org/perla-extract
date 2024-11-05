from deepdiff import DeepDiff

class Evaluations:
    deep_results = None
    score = None

    def __init__(self, truth, extraction):
        """Calculates a score between 0 and 1 using deepdiff. 1 being the best."""
        self.deep_results = DeepDiff(truth, extraction, ignore_order=True, get_deep_distance=True)
        self.score = 1-self.deep_results["deep_distance"] if "deep_distance" in self.deep_results else 1

