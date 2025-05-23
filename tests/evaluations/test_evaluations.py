from perovscribe.evaluations import Evaluations, match_cells
import pytest
from copy import deepcopy
from collections import defaultdict


def test_evaluations(
    truth,
    postprocessed_best_extraction,
    postprocessed_mid_extraction,
    postprocessed_worst_extraction,
):
    assert (
        Evaluations(
            truth,
            postprocessed_best_extraction,
            "gibberish",
            defaultdict(lambda: defaultdict(float)),
        ).score
        > Evaluations(
            truth,
            postprocessed_mid_extraction,
            "gibberish",
            defaultdict(lambda: defaultdict(float)),
        ).score
    )
    assert (
        Evaluations(
            truth,
            postprocessed_mid_extraction,
            "gibberish",
            defaultdict(lambda: defaultdict(float)),
        ).score
        > Evaluations(
            truth,
            postprocessed_worst_extraction,
            "gibberish",
            defaultdict(lambda: defaultdict(float)),
        ).score
    )


def test_matching(matching_1, matching_2):
    for match in match_cells(matching_1["cells"], matching_2["cells"], "gibberish"):
        assert str(match["truth"]) == str(match["extraction"])

    matching_1["cells"][1]["layers"] = []
    matching_2["cells"][0]["layers"] = []

    for match in match_cells(matching_1["cells"], matching_2["cells"], "gibberish"):
        assert str(match["truth"]) == str(match["extraction"])


def test_missing_layers_in_truth(truth):
    truth_copy = deepcopy(truth)
    del truth["cells"][0]["layers"]
    with pytest.raises(KeyError) as excinfo:
        Evaluations(
            truth, truth_copy, "gibberish", defaultdict(lambda: defaultdict(float))
        )
    assert str(excinfo.value) == "'layers'"


def test_important_missing_key_in_cell(truth):
    truth_copy = deepcopy(truth)
    del truth_copy["cells"][0]["ff"]
    Evaluations(truth, truth_copy, "gibberish", defaultdict(lambda: defaultdict(float)))
