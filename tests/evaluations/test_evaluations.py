from perovscribe.evaluations import Evaluations, match_cells


def test_evaluations(truth, best_extraction, mid_extraction, worst_extraction):
    assert (
        Evaluations(truth, best_extraction).score
        > Evaluations(truth, mid_extraction).score
    )
    assert (
        Evaluations(truth, mid_extraction).score
        > Evaluations(truth, worst_extraction).score
    )


def test_matching(matching_1, matching_2):
    for match in match_cells(matching_1["cells"], matching_2["cells"]):
        assert str(match["truth"]) == str(match["extraction"])
