from perovscribe.evaluations import Evaluations


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
    for match in Evaluations(matching_1, matching_2).match():
        assert str(match["truth"]) == str(match["extraction"])
