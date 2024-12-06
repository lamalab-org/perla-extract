import json
from typing import List
from dataclasses import dataclass
from deepdiff import DeepDiff
import numpy as np


@dataclass
class Tolerances:
    pce_tolerance: float = 0.01
    jsc_tolerance: float = 0.01
    voc_tolerance: float = 0.01
    ff_tolerance: float = 0.01


@dataclass
class DetailedScore:
    pce_mae: float
    jsc_mae: float
    voc_mae: float
    ff_mae: float

    @classmethod
    def calculate(cls, truth_cell: dict, pred_cell: dict) -> "DetailedScore":
        def safe_calculate_mae(truth_dict: dict, pred_dict: dict, param: str) -> float:
            try:
                truth_value = truth_dict[param]["value"]
                pred_value = pred_dict[param]["value"]
                return abs(truth_value - pred_value)
            except (KeyError, TypeError):
                return float("nan")

        pce_mae = safe_calculate_mae(truth_cell, pred_cell, "pce")
        jsc_mae = safe_calculate_mae(truth_cell, pred_cell, "jsc")
        voc_mae = safe_calculate_mae(truth_cell, pred_cell, "voc")
        ff_mae = safe_calculate_mae(truth_cell, pred_cell, "ff")

        return cls(pce_mae=pce_mae, jsc_mae=jsc_mae, voc_mae=voc_mae, ff_mae=ff_mae)

    @classmethod
    def aggregate(cls, scores: List["DetailedScore"]) -> "DetailedScore":
        def safe_mean(values: List[float]) -> float:
            values = [v for v in values if not np.isnan(v)]
            return np.mean(values) if values else float("nan")

        return cls(
            pce_mae=safe_mean([s.pce_mae for s in scores]),
            jsc_mae=safe_mean([s.jsc_mae for s in scores]),
            voc_mae=safe_mean([s.voc_mae for s in scores]),
            ff_mae=safe_mean([s.ff_mae for s in scores]),
        )


@dataclass
class DeviceLevelScore:
    device_id: str
    deepdiff_overall: dict
    deepdiff_stack: dict
    deepdiff_layers: dict
    critical_params_precision: float
    detailed_score: DetailedScore


@dataclass
class OverallScore:
    num_devices_found: int
    num_devices_matched: int
    recall: float
    device_scores: List[DeviceLevelScore]
    avg_critical_precision: float
    sum_critical_precision: float
    detailed_aggregate: DetailedScore

    @classmethod
    def calculate(
        cls,
        truth_cells: List[dict],
        pred_cells: List[dict],
        tolerances: Tolerances = Tolerances(),
    ) -> "OverallScore":
        matches = match_devices(truth_cells, pred_cells)
        device_scores = []
        detailed_scores = []

        for truth, pred in matches:
            detailed_score = DetailedScore.calculate(truth, pred)
            detailed_scores.append(detailed_score)
            critical_precision = check_critical_params_within_tolerance(
                truth, pred, tolerances
            )

            device_score = DeviceLevelScore(
                device_id=truth.get("additional_notes", "unknown"),
                deepdiff_overall=DeepDiff(truth, pred, ignore_order=True),
                deepdiff_stack=DeepDiff(
                    truth["cell_stack"], pred["cell_stack"], ignore_order=True
                ),
                deepdiff_layers=DeepDiff(
                    truth["layers"], pred["layers"], ignore_order=True
                ),
                critical_params_precision=critical_precision,
                detailed_score=detailed_score,
            )
            device_scores.append(device_score)

        recall = len(matches) / len(truth_cells) if truth_cells else 0
        avg_precision = (
            np.mean([d.critical_params_precision for d in device_scores])
            if device_scores
            else float("nan")
        )
        sum_precision = sum(d.critical_params_precision for d in device_scores)
        detailed_aggregate = DetailedScore.aggregate(detailed_scores)

        return cls(
            num_devices_found=len(pred_cells),
            num_devices_matched=len(matches),
            recall=recall,
            device_scores=device_scores,
            avg_critical_precision=avg_precision,
            sum_critical_precision=sum_precision,
            detailed_aggregate=detailed_aggregate,
        )


def check_critical_params_within_tolerance(
    truth: dict, pred: dict, tolerances: Tolerances
) -> float:
    try:

        def safe_check_tolerance(
            truth_dict: dict, pred_dict: dict, param: str, tolerance: float
        ) -> bool:
            try:
                truth_value = truth_dict[param]["value"]
                pred_value = pred_dict[param]["value"]
                return abs(truth_value - pred_value) / truth_value <= tolerance
            except (KeyError, TypeError, ZeroDivisionError):
                return False

        pce_match = safe_check_tolerance(truth, pred, "pce", tolerances.pce_tolerance)
        jsc_match = safe_check_tolerance(truth, pred, "jsc", tolerances.jsc_tolerance)
        voc_match = safe_check_tolerance(truth, pred, "voc", tolerances.voc_tolerance)
        ff_match = safe_check_tolerance(truth, pred, "ff", tolerances.ff_tolerance)
        stack_match = truth["cell_stack"] == pred["cell_stack"]

        return float(pce_match and jsc_match and voc_match and ff_match and stack_match)
    except (KeyError, TypeError, ZeroDivisionError):
        return 0.0


def match_devices(truth_cells: List[dict], pred_cells: List[dict]) -> List[tuple]:
    matches = []
    truth_dict = {cell.get("additional_notes"): cell for cell in truth_cells}
    pred_dict = {cell.get("additional_notes"): cell for cell in pred_cells}

    for note in truth_dict:
        if note in pred_dict:
            matches.append((truth_dict[note], pred_dict[note]))
    return matches


def generate_detailed_report(overall_score: OverallScore) -> str:
    report = [
        "DETAILED MATCHING ANALYSIS REPORT",
        "=" * 80,
        "",
        "1. DEVICE COUNTS",
        "-" * 80,
    ]
    report.extend(
        [
            f"Total truth devices: {overall_score.num_devices_found}",
            f"Total extracted devices: {overall_score.num_devices_matched}",
            f"Total matched pairs: {len(overall_score.device_scores)}",
            "",
            "2. MATCHED PAIRS ANALYSIS",
            "-" * 80,
            "",
        ]
    )

    for idx, device_score in enumerate(overall_score.device_scores, 1):
        truth_cell = next(
            (
                t
                for t in truth_cells
                if t.get("additional_notes") == device_score.device_id
            ),
            None,
        )
        pred_cell = next(
            (
                p
                for p in extracted_cells
                if p.get("additional_notes") == device_score.device_id
            ),
            None,
        )

        if truth_cell and pred_cell:

            def safe_get_value(cell: dict, param: str) -> str:
                try:
                    return f"{cell[param]['value']}"
                except (KeyError, TypeError):
                    return "N/A"

            def safe_calc_relative_error(truth: dict, pred: dict, param: str) -> str:
                try:
                    truth_val = truth[param]["value"]
                    pred_val = pred[param]["value"]
                    return f"{abs(truth_val - pred_val) / truth_val * 100:.4f}%"
                except (KeyError, TypeError, ZeroDivisionError):
                    return "N/A"

            report.extend(
                [
                    f"Pair {idx}:",
                    "  Truth Device:",
                    f"    ID/Notes: {device_score.device_id}",
                    f"    PCE: {safe_get_value(truth_cell, 'pce')} %",
                    f"    JSC: {safe_get_value(truth_cell, 'jsc')} mA cm^-2",
                    f"    VOC: {safe_get_value(truth_cell, 'voc')} V",
                    f"    FF: {safe_get_value(truth_cell, 'ff')}",
                    f"    Stack: {', '.join(truth_cell['cell_stack'])}",
                    f"    Composition: {truth_cell.get('perovskite_composition', 'N/A')}",
                    "  Extracted Device:",
                    f"    ID/Notes: {device_score.device_id}",
                    f"    PCE: {safe_get_value(pred_cell, 'pce')} %",
                    f"    JSC: {safe_get_value(pred_cell, 'jsc')} mA cm^-2",
                    f"    VOC: {safe_get_value(pred_cell, 'voc')} V",
                    f"    FF: {safe_get_value(pred_cell, 'ff')}",
                    f"    Stack: {', '.join(pred_cell['cell_stack'])}",
                    f"    Composition: {pred_cell.get('perovskite_composition', 'N/A')}",
                    f"    PCE Absolute Difference: {device_score.detailed_score.pce_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.pce_mae)
                    else "    PCE Absolute Difference: N/A",
                    f"    PCE Relative Error: {safe_calc_relative_error(truth_cell, pred_cell, 'pce')}",
                    f"    JSC Absolute Difference: {device_score.detailed_score.jsc_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.jsc_mae)
                    else "    JSC Absolute Difference: N/A",
                    f"    JSC Relative Error: {safe_calc_relative_error(truth_cell, pred_cell, 'jsc')}",
                    f"    VOC Absolute Difference: {device_score.detailed_score.voc_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.voc_mae)
                    else "    VOC Absolute Difference: N/A",
                    f"    VOC Relative Error: {safe_calc_relative_error(truth_cell, pred_cell, 'voc')}",
                    f"    Stack Similarity: {1.0 if not device_score.deepdiff_stack else 0.0:.2f}",
                    "",
                ]
            )

    return "\n".join(report)


def generate_performance_report(overall_score: OverallScore) -> str:
    def format_mae(value: float) -> str:
        return f"{value:.3f}" if not np.isnan(value) else "N/A"

    results = [
        "=== Overall Extraction Performance ===",
        f"Devices Found: {overall_score.num_devices_found}",
        f"Devices Matched: {overall_score.num_devices_matched}",
        f"Recall: {overall_score.recall:.3f}",
        f"Average Critical Parameters Precision: {overall_score.avg_critical_precision:.3f}",
        f"Sum Critical Parameters Precision: {overall_score.sum_critical_precision:.3f}",
        "\n=== Aggregate Parameter Extraction Accuracy ===",
        f"PCE MAE: {format_mae(overall_score.detailed_aggregate.pce_mae)}",
        f"JSC MAE: {format_mae(overall_score.detailed_aggregate.jsc_mae)}",
        f"VOC MAE: {format_mae(overall_score.detailed_aggregate.voc_mae)}",
        f"FF MAE: {format_mae(overall_score.detailed_aggregate.ff_mae)}",
        "\n=== Device Level Scores ===",
    ]

    for device in overall_score.device_scores:
        results.extend(
            [
                f"\nDevice: {device.device_id}",
                f"Critical Parameters Precision: {device.critical_params_precision:.3f}",
                f"Stack differences: {len(device.deepdiff_stack.get('values_changed', {}))}",
                f"Layer differences: {len(device.deepdiff_layers.get('values_changed', {}))}",
                f"PCE MAE: {format_mae(device.detailed_score.pce_mae)}",
                f"JSC MAE: {format_mae(device.detailed_score.jsc_mae)}",
                f"VOC MAE: {format_mae(device.detailed_score.voc_mae)}",
                f"FF MAE: {format_mae(device.detailed_score.ff_mae)}",
            ]
        )

    return "\n".join(results)


def main():
    with open("truth_test_file.json", "r") as f:
        truth_data = json.load(f)
    with open("extraction_test_file.json", "r") as f:
        extracted_data = json.load(f)

    global truth_cells, extracted_cells
    truth_cells = truth_data["cells"]
    extracted_cells = extracted_data["cells"]

    tolerances = Tolerances(
        pce_tolerance=0.01, jsc_tolerance=0.01, voc_tolerance=0.01, ff_tolerance=0.01
    )

    scores = OverallScore.calculate(truth_cells, extracted_cells, tolerances)
    detailed_report = generate_detailed_report(scores)
    performance_report = generate_performance_report(scores)

    with open("detailed_analysis_report.txt", "w") as f:
        f.write(detailed_report)
    with open("overall_performance_analysis_report.txt", "w") as f:
        f.write(performance_report)


if __name__ == "__main__":
    main()
