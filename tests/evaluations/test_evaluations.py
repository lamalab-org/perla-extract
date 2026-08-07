from perla_extract.evaluations import Evaluations, calculate_micro_metrics
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
    for match in Evaluations({"cells":{}},{"cells":{}},"gibberish",defaultdict(lambda: defaultdict(float)))._match_cells(matching_1["cells"], matching_2["cells"], "gibberish"):
        assert str(match["truth"]) == str(match["extraction"])

    matching_1["cells"][1]["layers"] = []
    matching_2["cells"][0]["layers"] = []

    for match in Evaluations({"cells":{}},{"cells":{}},"gibberish",defaultdict(lambda: defaultdict(float)))._match_cells(matching_1["cells"], matching_2["cells"], "gibberish"):
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


def _evaluate_cells(truth_cells, extraction_cells):
    return Evaluations(
        {"cells": truth_cells},
        {"cells": extraction_cells},
        "test.json",
        defaultdict(lambda: defaultdict(float)),
    )


def test_missing_device_is_not_counted_as_a_match():
    truth_cell = {
        "pce": {"value": 20.0, "unit": "%"},
        "layers": [{"name": "ITO", "functionality": "Substrate"}],
    }
    evaluation = _evaluate_cells([truth_cell, deepcopy(truth_cell)], [truth_cell])

    assert evaluation.devices_matched == 1
    assert evaluation.recall_devices == 0.5
    assert evaluation.recalls_average < 1.0


def test_extra_device_reduces_precision():
    truth_cell = {
        "pce": {"value": 20.0, "unit": "%"},
        "layers": [{"name": "ITO", "functionality": "Substrate"}],
    }
    extra_cell = {"pce": {"value": 5.0, "unit": "%"}, "layers": []}
    evaluation = _evaluate_cells([truth_cell], [truth_cell, extra_cell])

    assert evaluation.precision_devices == 0.5
    assert evaluation.precisions_average < 1.0
    assert evaluation.recalls_average == 1.0


def test_null_placeholders_are_not_scored_as_facts():
    truth_cell = {
        "pce": {"value": 20.0, "unit": "%"},
        "active_area": None,
        "layers": [],
    }
    extraction_cell = {
        "pce": {"value": 20.0, "unit": "%"},
        "active_area": None,
        "layers": [],
    }
    evaluation = _evaluate_cells([truth_cell], [extraction_cell])

    assert evaluation.precisions_average == 1.0
    assert evaluation.recalls_average == 1.0


def test_wrong_value_is_both_false_positive_and_false_negative():
    evaluation = _evaluate_cells(
        [{"pce": {"value": 20.0, "unit": "%"}, "layers": []}],
        [{"pce": {"value": 10.0, "unit": "%"}, "layers": []}],
    )

    assert evaluation.precisions_average == 0.5
    assert evaluation.recalls_average == 0.5


def test_zero_tolerance_requires_an_exact_numeric_match():
    metrics = defaultdict(lambda: defaultdict(float))
    evaluation = Evaluations(
        {"cells": [{"pce": {"value": 20.0}, "layers": []}]},
        {"cells": [{"pce": {"value": 20.005}, "layers": []}]},
        "test.json",
        metrics,
        precision_tolerances={"pce": 0.0},
    )

    assert evaluation.precisions_average < 1.0
    assert evaluation.recalls_average < 1.0


def test_micro_metrics_use_global_fact_counts():
    metrics = {
        "a.json": {"pce:value": {"TP": 9, "FP": 1, "FN": 3}},
        "b.json": {"layers:name": {"TP": 1, "FP": 9, "FN": 1}},
    }

    result = calculate_micro_metrics(metrics)

    assert result["precision"] == 0.5
    assert result["recall"] == 10 / 14
    assert result["f1"] == 2 * 0.5 * (10 / 14) / (0.5 + (10 / 14))
