from perovscribe.evaluations import Evaluations, match_cells


def test_evaluations(
    truth,
    postprocessed_best_extraction,
    postprocessed_mid_extraction,
    postprocessed_worst_extraction,
):
    assert (
        Evaluations(truth, postprocessed_best_extraction).score
        > Evaluations(truth, postprocessed_mid_extraction).score
    )
    assert (
        Evaluations(truth, postprocessed_mid_extraction).score
        > Evaluations(truth, postprocessed_worst_extraction).score
    )


def test_matching(matching_1, matching_2):
    for match in match_cells(matching_1["cells"], matching_2["cells"]):
        assert str(match["truth"]) == str(match["extraction"])

    matching_1["cells"][1]["layers"] = None
    matching_2["cells"][0]["layers"] = None

    for match in match_cells(matching_1["cells"], matching_2["cells"]):
        assert str(match["truth"]) == str(match["extraction"])
