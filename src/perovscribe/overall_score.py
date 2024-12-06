from typing import List, Dict, Union, Optional, Any, Tuple
from dataclasses import dataclass
from deepdiff import DeepDiff
import numpy as np
import json


PARAMETER_CONFIG = {
    "pce": {
        "display_name": "PCE",
        "units": "%",
        "getter": lambda cell: cell.get("pce", {}).get("value", "N/A"),
        "safe_getter": lambda cell: cell.get("pce", {}).get("value"),
    },
    "jsc": {
        "display_name": "JSC",
        "units": "mA cm^-2",
        "getter": lambda cell: cell.get("jsc", {}).get("value", "N/A"),
        "safe_getter": lambda cell: cell.get("jsc", {}).get("value"),
    },
    "voc": {
        "display_name": "VOC",
        "units": "V",
        "getter": lambda cell: cell.get("voc", {}).get("value", "N/A"),
        "safe_getter": lambda cell: cell.get("voc", {}).get("value"),
    },
    "ff": {
        "display_name": "FF",
        "units": "",
        "getter": lambda cell: cell.get("ff", {}).get("value", "N/A"),
        "safe_getter": lambda cell: cell.get("ff", {}).get("value"),
    },
}

LAYER_FIELDS = {
    "name": "Name",
    "functionality": "Functionality",
    "thickness": "Thickness",
    "deposition": "Deposition Method",
}


@dataclass
class ToleranceConfig:
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
    def calculate(
        cls, truth_cell: Dict[str, Any], pred_cell: Dict[str, Any]
    ) -> "DetailedScore":
        maes = {}
        for param_key, config in PARAMETER_CONFIG.items():
            truth_val = config["safe_getter"](truth_cell)
            pred_val = config["safe_getter"](pred_cell)

            if truth_val is None or pred_val is None:
                maes[f"{param_key}_mae"] = np.nan
            else:
                maes[f"{param_key}_mae"] = abs(truth_val - pred_val)

        return cls(**maes)

    @classmethod
    def aggregate(cls, scores: List["DetailedScore"]) -> "DetailedScore":
        aggregated_maes = {}
        for param_key in PARAMETER_CONFIG.keys():
            values = [getattr(s, f"{param_key}_mae") for s in scores]
            aggregated_maes[f"{param_key}_mae"] = np.nanmean(values)
        return cls(**aggregated_maes)


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
        tolerance_config: Optional[ToleranceConfig] = None,
    ) -> "OverallScore":
        tolerance_config = tolerance_config or ToleranceConfig()
        matches = match_devices(truth_cells, pred_cells)
        device_scores = []
        detailed_scores = []

        for truth, pred in matches:
            detailed_score = DetailedScore.calculate(truth, pred)
            detailed_scores.append(detailed_score)
            critical_precision = check_critical_params_within_tolerance(
                truth, pred, tolerance_config
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
        avg_precision = np.mean([d.critical_params_precision for d in device_scores])
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
    truth: Dict[str, Any], pred: Dict[str, Any], tolerance_config: ToleranceConfig
) -> float:
    try:
        matches = []
        for param_key, config in PARAMETER_CONFIG.items():
            truth_val = config["safe_getter"](truth)
            pred_val = config["safe_getter"](pred)

            if truth_val is not None and pred_val is not None:
                diff = abs(truth_val - pred_val) / truth_val
                tolerance = getattr(tolerance_config, f"{param_key}_tolerance")
                matches.append(diff <= tolerance)

        stack_match = truth["cell_stack"] == pred["cell_stack"]
        matches.append(stack_match)

        return float(all(matches)) if matches else 0.0
    except (KeyError, TypeError, ZeroDivisionError):
        return 0.0


def match_devices(
    truth_cells: List[Dict[str, Any]], pred_cells: List[Dict[str, Any]]
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    matches = []
    truth_dict = {cell.get("additional_notes"): cell for cell in truth_cells}
    pred_dict = {cell.get("additional_notes"): cell for cell in pred_cells}

    for note in truth_dict:
        if note in pred_dict:
            matches.append((truth_dict[note], pred_dict[note]))
    return matches


def get_layer_differences(
    truth_layer: Dict[str, Any], pred_layer: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    differences = {}
    for field, display_name in LAYER_FIELDS.items():
        truth_value = truth_layer.get(field)
        pred_value = pred_layer.get(field)

        if truth_value != pred_value:
            differences[field] = {
                "display_name": display_name,
                "truth": truth_value,
                "extracted": pred_value,
            }

    return differences


def format_layer_difference(
    layer_index: int, differences: Dict[str, Dict[str, Any]]
) -> List[str]:
    lines = [f"      Layer {layer_index} Differences:"]

    for field, diff in differences.items():
        truth_val = "None" if diff["truth"] is None else diff["truth"]
        extracted_val = "None" if diff["extracted"] is None else diff["extracted"]

        lines.extend(
            [
                f"        {diff['display_name']}:",
                f"          Groundtruth: {truth_val}",
                f"          Extracted: {extracted_val}",
            ]
        )

    return lines


def analyze_layer_differences(
    truth_cell: Dict[str, Any], pred_cell: Dict[str, Any]
) -> List[Dict[str, Union[int, Dict[str, Dict[str, Any]]]]]:
    layer_differences = []

    for i, (truth_layer, pred_layer) in enumerate(
        zip(truth_cell["layers"], pred_cell["layers"])
    ):
        differences = get_layer_differences(truth_layer, pred_layer)
        if differences:
            layer_differences.append({"index": i, "differences": differences})

    return layer_differences


def format_all_differences(
    layer_differences: List[Dict[str, Union[int, Dict[str, Dict[str, Any]]]]],
) -> List[str]:
    if not layer_differences:
        return ["    No layer differences found."]

    lines = ["    Layer Difference Details:"]
    for diff in layer_differences:
        lines.extend(format_layer_difference(diff["index"], diff["differences"]))

    return lines


def add_layer_difference_section(
    report: List[str], truth_cell: Dict[str, Any], pred_cell: Dict[str, Any]
):
    layer_differences = analyze_layer_differences(truth_cell, pred_cell)
    num_differences = len(layer_differences)

    report.append(f"    Layer differences: {num_differences}")
    if num_differences > 0:
        report.extend(format_all_differences(layer_differences))
    report.append("")


def generate_detailed_report(
    overall_score: OverallScore,
    tolerance_config: ToleranceConfig,
    truth_cells: List[Dict[str, Any]],
    extracted_cells: List[Dict[str, Any]],
) -> str:
    def format_device_section(device_score, cell, prefix=""):
        lines = []
        lines.append(f"{prefix}ID/Notes: {device_score.device_id}")

        for param_key, config in PARAMETER_CONFIG.items():
            value = config["getter"](cell)
            unit_str = f" {config['units']}" if config["units"] else ""
            lines.append(f"{prefix}{config['display_name']}: {value}{unit_str}")

        lines.append(f"{prefix}Stack: {', '.join(cell['cell_stack'])}")
        lines.append(
            f"{prefix}Composition: {cell.get('perovskite_composition', 'N/A')}"
        )
        return lines

    def format_metrics_section(device_score, truth_cell, pred_cell, tolerance_config):
        lines = []
        for param_key, config in PARAMETER_CONFIG.items():
            truth_val = config["safe_getter"](truth_cell)
            pred_val = config["safe_getter"](pred_cell)

            if truth_val is not None and pred_val is not None:
                rel_error = abs(truth_val - pred_val) / truth_val * 100
                tolerance = getattr(tolerance_config, f"{param_key}_tolerance")
                match = (abs(truth_val - pred_val) / truth_val) <= tolerance

                mae = getattr(device_score.detailed_score, f"{param_key}_mae")

                lines.extend(
                    [
                        f"    {config['display_name']} Absolute Difference: {mae:.4f}"
                        if not np.isnan(mae)
                        else f"    {config['display_name']} Absolute Difference: N/A",
                        f"    {config['display_name']} Relative Error: {rel_error:.4f}%"
                        if not np.isnan(rel_error)
                        else f"    {config['display_name']} Relative Error: N/A",
                        f"    {config['display_name']} Match (within {tolerance*100}%): {match}",
                    ]
                )

        lines.append(
            f"    Stack Similarity: {1.0 if not device_score.deepdiff_stack else 0.0:.2f}"
        )
        return lines

    report = [
        "DETAILED MATCHING ANALYSIS REPORT",
        "=" * 80,
        "",
        "1. DEVICE COUNTS",
        "-" * 80,
        f"Total truth devices: {overall_score.num_devices_found}",
        f"Total extracted devices: {overall_score.num_devices_matched}",
        f"Total matched pairs: {len(overall_score.device_scores)}",
        "",
        "2. MATCHED PAIRS ANALYSIS",
        "-" * 80,
        "",
    ]

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
            report.append(f"Pair {idx}:")
            report.append("  Truth Device:")
            report.extend(format_device_section(device_score, truth_cell, "    "))
            report.append("  Extracted Device:")
            report.extend(format_device_section(device_score, pred_cell, "    "))
            report.append("  Comparison Metrics:")
            report.extend(
                format_metrics_section(
                    device_score, truth_cell, pred_cell, tolerance_config
                )
            )
            add_layer_difference_section(report, truth_cell, pred_cell)

    return "\n".join(report)


def generate_performance_report(overall_score: OverallScore) -> str:
    lines = [
        "=== Overall Extraction Performance ===",
        f"Devices Found: {overall_score.num_devices_found}",
        f"Devices Matched: {overall_score.num_devices_matched}",
        f"Recall: {overall_score.recall:.3f}",
        f"Average Critical Parameters Precision: {overall_score.avg_critical_precision:.3f}",
        f"Sum Critical Parameters Precision: {overall_score.sum_critical_precision:.3f}",
        "\n=== Aggregate Parameter Extraction Accuracy ===",
    ]

    for param_key, config in PARAMETER_CONFIG.items():
        mae = getattr(overall_score.detailed_aggregate, f"{param_key}_mae")
        lines.append(
            f"{config['display_name']} MAE: {mae:.3f}"
            if not np.isnan(mae)
            else f"{config['display_name']} MAE: N/A"
        )

    lines.append("\n=== Device Level Scores ===")

    for device in overall_score.device_scores:
        device_lines = [
            f"\nDevice: {device.device_id}",
            f"Critical Parameters Precision: {device.critical_params_precision:.3f}",
            f"Stack differences: {len(device.deepdiff_stack.get('values_changed', {}))}",
            f"Layer differences: {len(device.deepdiff_layers.get('values_changed', {}))}",
        ]

        for param_key, config in PARAMETER_CONFIG.items():
            mae = getattr(device.detailed_score, f"{param_key}_mae")
            device_lines.append(
                f"{config['display_name']} MAE: {mae:.3f}"
                if not np.isnan(mae)
                else f"{config['display_name']} MAE: N/A"
            )

        lines.extend(device_lines)

    return "\n".join(lines)


def main():
    with open("truth_test_file.json", "r") as f:
        truth_data = json.load(f)
    with open("extraction_test_file.json", "r") as f:
        extracted_data = json.load(f)

    truth_cells = truth_data["cells"]
    extracted_cells = extracted_data["cells"]

    tolerance_config = ToleranceConfig(
        pce_tolerance=0.01, jsc_tolerance=0.01, voc_tolerance=0.01, ff_tolerance=0.01
    )

    scores = OverallScore.calculate(truth_cells, extracted_cells, tolerance_config)

    detailed_report = generate_detailed_report(
        scores, tolerance_config, truth_cells, extracted_cells
    )
    performance_report = generate_performance_report(scores)

    with open("detailed_analysis_report_2.txt", "w") as f:
        f.write(detailed_report)
    with open("overall_performance_analysis_report_2.txt", "w") as f:
        f.write(performance_report)


if __name__ == "__main__":
    main()
