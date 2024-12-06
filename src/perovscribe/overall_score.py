from typing import List, Dict, Union, Optional, Any, Tuple
from dataclasses import dataclass
from deepdiff import DeepDiff
import numpy as np


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
        def safe_get_value(cell: Dict[str, Any], param: str) -> Optional[float]:
            try:
                return cell.get(param, {}).get("value")
            except AttributeError:
                return None

        def calculate_mae(
            truth_val: Optional[float], pred_val: Optional[float]
        ) -> float:
            if truth_val is None or pred_val is None:
                return np.nan
            return abs(truth_val - pred_val)

        pce_mae = calculate_mae(
            safe_get_value(truth_cell, "pce"), safe_get_value(pred_cell, "pce")
        )
        jsc_mae = calculate_mae(
            safe_get_value(truth_cell, "jsc"), safe_get_value(pred_cell, "jsc")
        )
        voc_mae = calculate_mae(
            safe_get_value(truth_cell, "voc"), safe_get_value(pred_cell, "voc")
        )
        ff_mae = calculate_mae(
            safe_get_value(truth_cell, "ff"), safe_get_value(pred_cell, "ff")
        )

        return cls(pce_mae=pce_mae, jsc_mae=jsc_mae, voc_mae=voc_mae, ff_mae=ff_mae)

    @classmethod
    def aggregate(cls, scores: List["DetailedScore"]) -> "DetailedScore":
        return cls(
            pce_mae=np.nanmean([s.pce_mae for s in scores]),
            jsc_mae=np.nanmean([s.jsc_mae for s in scores]),
            voc_mae=np.nanmean([s.voc_mae for s in scores]),
            ff_mae=np.nanmean([s.ff_mae for s in scores]),
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
        has_pce = "pce" in pred and "value" in pred["pce"]
        has_jsc = "jsc" in pred and "value" in pred["jsc"]
        has_voc = "voc" in pred and "value" in pred["voc"]
        has_ff = "ff" in pred and "value" in pred["ff"]

        if not any([has_pce, has_jsc, has_voc, has_ff]):
            return 0.0

        matches = []
        if has_pce:
            pce_diff = (
                abs(truth["pce"]["value"] - pred["pce"]["value"])
                / truth["pce"]["value"]
            )
            matches.append(pce_diff <= tolerance_config.pce_tolerance)
        if has_jsc:
            jsc_diff = (
                abs(truth["jsc"]["value"] - pred["jsc"]["value"])
                / truth["jsc"]["value"]
            )
            matches.append(jsc_diff <= tolerance_config.jsc_tolerance)
        if has_voc:
            voc_diff = (
                abs(truth["voc"]["value"] - pred["voc"]["value"])
                / truth["voc"]["value"]
            )
            matches.append(voc_diff <= tolerance_config.voc_tolerance)
        if has_ff:
            ff_diff = (
                abs(truth["ff"]["value"] - pred["ff"]["value"]) / truth["ff"]["value"]
            )
            matches.append(ff_diff <= tolerance_config.ff_tolerance)

        stack_match = truth["cell_stack"] == pred["cell_stack"]
        matches.append(stack_match)

        return float(all(matches))
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
    fields_to_check = {
        "name": "Name",
        "functionality": "Functionality",
        "thickness": "Thickness",
        "deposition": "Deposition Method",
    }

    for field, display_name in fields_to_check.items():
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

        lines.append(f"        {diff['display_name']}:")
        lines.append(f"          Groundtruth: {truth_val}")
        lines.append(f"          Extracted: {extracted_val}")

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
    overall_score: OverallScore, tolerance_config: ToleranceConfig
) -> str:
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
            pce_rel_error = (
                abs(truth_cell["pce"]["value"] - pred_cell["pce"]["value"])
                / truth_cell["pce"]["value"]
                * 100
                if "pce" in pred_cell and "value" in pred_cell["pce"]
                else np.nan
            )
            jsc_rel_error = (
                abs(truth_cell["jsc"]["value"] - pred_cell["jsc"]["value"])
                / truth_cell["jsc"]["value"]
                * 100
                if "jsc" in pred_cell and "value" in pred_cell["jsc"]
                else np.nan
            )
            voc_rel_error = (
                abs(truth_cell["voc"]["value"] - pred_cell["voc"]["value"])
                / truth_cell["voc"]["value"]
                * 100
                if "voc" in pred_cell and "value" in pred_cell["voc"]
                else np.nan
            )
            ff_rel_error = (
                abs(truth_cell["ff"]["value"] - pred_cell["ff"]["value"])
                / truth_cell["ff"]["value"]
                * 100
                if "ff" in pred_cell and "value" in pred_cell["ff"]
                else np.nan
            )

            pce_match = (
                abs(truth_cell["pce"]["value"] - pred_cell["pce"]["value"])
                / truth_cell["pce"]["value"]
                <= tolerance_config.pce_tolerance
                if "pce" in pred_cell and "value" in pred_cell["pce"]
                else False
            )
            jsc_match = (
                abs(truth_cell["jsc"]["value"] - pred_cell["jsc"]["value"])
                / truth_cell["jsc"]["value"]
                <= tolerance_config.jsc_tolerance
                if "jsc" in pred_cell and "value" in pred_cell["jsc"]
                else False
            )
            voc_match = (
                abs(truth_cell["voc"]["value"] - pred_cell["voc"]["value"])
                / truth_cell["voc"]["value"]
                <= tolerance_config.voc_tolerance
                if "voc" in pred_cell and "value" in pred_cell["voc"]
                else False
            )
            ff_match = (
                abs(truth_cell["ff"]["value"] - pred_cell["ff"]["value"])
                / truth_cell["ff"]["value"]
                <= tolerance_config.ff_tolerance
                if "ff" in pred_cell and "value" in pred_cell["ff"]
                else False
            )

            stack_similarity = 1.0 if not device_score.deepdiff_stack else 0.0

            report.extend(
                [
                    f"Pair {idx}:",
                    "  Truth Device:",
                    f"    ID/Notes: {device_score.device_id}",
                    f"    PCE: {truth_cell['pce']['value']} %",
                    f"    JSC: {truth_cell['jsc']['value']} mA cm^-2",
                    f"    VOC: {truth_cell['voc']['value']} V",
                    f"    FF: {truth_cell['ff']['value']}",
                    f"    Stack: {', '.join(truth_cell['cell_stack'])}",
                    f"    Composition: {truth_cell.get('perovskite_composition', 'N/A')}",
                    "  Extracted Device:",
                    f"    ID/Notes: {device_score.device_id}",
                    f"    PCE: {pred_cell.get('pce', {}).get('value', 'N/A')} %",
                    f"    JSC: {pred_cell.get('jsc', {}).get('value', 'N/A')} mA cm^-2",
                    f"    VOC: {pred_cell.get('voc', {}).get('value', 'N/A')} V",
                    f"    FF: {pred_cell.get('ff', {}).get('value', 'N/A')}",
                    f"    Stack: {', '.join(pred_cell['cell_stack'])}",
                    f"    Composition: {pred_cell.get('perovskite_composition', 'N/A')}",
                    "  Comparison Metrics:",
                    f"    PCE Absolute Difference: {device_score.detailed_score.pce_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.pce_mae)
                    else "    PCE Absolute Difference: N/A",
                    f"    PCE Relative Error: {pce_rel_error:.4f}%"
                    if not np.isnan(pce_rel_error)
                    else "    PCE Relative Error: N/A",
                    f"    PCE Match (within {tolerance_config.pce_tolerance*100}%): {pce_match}",
                    f"    JSC Absolute Difference: {device_score.detailed_score.jsc_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.jsc_mae)
                    else "    JSC Absolute Difference: N/A",
                    f"    JSC Relative Error: {jsc_rel_error:.4f}%"
                    if not np.isnan(jsc_rel_error)
                    else "    JSC Relative Error: N/A",
                    f"    JSC Match (within {tolerance_config.jsc_tolerance*100}%): {jsc_match}",
                    f"    VOC Absolute Difference: {device_score.detailed_score.voc_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.voc_mae)
                    else "    VOC Absolute Difference: N/A",
                    f"    VOC Relative Error: {voc_rel_error:.4f}%"
                    if not np.isnan(voc_rel_error)
                    else "    VOC Relative Error: N/A",
                    f"    VOC Match (within {tolerance_config.voc_tolerance*100}%): {voc_match}",
                    f"    FF Match (within {tolerance_config.ff_tolerance*100}%): {ff_match}",
                    f"    FF Absolute Difference: {device_score.detailed_score.ff_mae:.4f}"
                    if not np.isnan(device_score.detailed_score.ff_mae)
                    else "    FF Absolute Difference: N/A",
                    f"    FF Relative Error: {ff_rel_error:.4f}%"
                    if not np.isnan(ff_rel_error)
                    else "    FF Relative Error: N/A",
                    f"    Stack Similarity: {stack_similarity:.2f}",
                ]
            )

            add_layer_difference_section(report, truth_cell, pred_cell)

    return "\n".join(report)


def generate_performance_report(overall_score: OverallScore) -> str:
    results = [
        "=== Overall Extraction Performance ===",
        f"Devices Found: {overall_score.num_devices_found}",
        f"Devices Matched: {overall_score.num_devices_matched}",
        f"Recall: {overall_score.recall:.3f}",
        f"Average Critical Parameters Precision: {overall_score.avg_critical_precision:.3f}",
        f"Sum Critical Parameters Precision: {overall_score.sum_critical_precision:.3f}",
        "\n=== Aggregate Parameter Extraction Accuracy ===",
        f"PCE MAE: {overall_score.detailed_aggregate.pce_mae:.3f}"
        if not np.isnan(overall_score.detailed_aggregate.pce_mae)
        else "PCE MAE: N/A",
        f"JSC MAE: {overall_score.detailed_aggregate.jsc_mae:.3f}"
        if not np.isnan(overall_score.detailed_aggregate.jsc_mae)
        else "JSC MAE: N/A",
        f"VOC MAE: {overall_score.detailed_aggregate.voc_mae:.3f}"
        if not np.isnan(overall_score.detailed_aggregate.voc_mae)
        else "VOC MAE: N/A",
        f"FF MAE: {overall_score.detailed_aggregate.ff_mae:.3f}"
        if not np.isnan(overall_score.detailed_aggregate.ff_mae)
        else "FF MAE: N/A",
        "\n=== Device Level Scores ===",
    ]

    for device in overall_score.device_scores:
        results.extend(
            [
                f"\nDevice: {device.device_id}",
                f"Critical Parameters Precision: {device.critical_params_precision:.3f}",
                f"Stack differences: {len(device.deepdiff_stack.get('values_changed', {}))}",
                f"Layer differences: {len(device.deepdiff_layers.get('values_changed', {}))}",
                f"PCE MAE: {device.detailed_score.pce_mae:.3f}"
                if not np.isnan(device.detailed_score.pce_mae)
                else "PCE MAE: N/A",
                f"JSC MAE: {device.detailed_score.jsc_mae:.3f}"
                if not np.isnan(device.detailed_score.jsc_mae)
                else "JSC MAE: N/A",
                f"VOC MAE: {device.detailed_score.voc_mae:.3f}"
                if not np.isnan(device.detailed_score.voc_mae)
                else "VOC MAE: N/A",
                f"FF MAE: {device.detailed_score.ff_mae:.3f}"
                if not np.isnan(device.detailed_score.ff_mae)
                else "FF MAE: N/A",
            ]
        )

    return "\n".join(results)


def main():
    import json

    with open("truth_test_file.json", "r") as f:
        truth_data = json.load(f)
    with open("extraction_test_file.json", "r") as f:
        extracted_data = json.load(f)

    global truth_cells, extracted_cells
    truth_cells = truth_data["cells"]
    extracted_cells = extracted_data["cells"]

    tolerance_config = ToleranceConfig(
        pce_tolerance=0.01, jsc_tolerance=0.01, voc_tolerance=0.01, ff_tolerance=0.01
    )

    scores = OverallScore.calculate(truth_cells, extracted_cells, tolerance_config)

    detailed_report = generate_detailed_report(scores, tolerance_config)
    performance_report = generate_performance_report(scores)

    with open("detailed_analysis_report.txt", "w") as f:
        f.write(detailed_report)
    with open("overall_performance_analysis_report.txt", "w") as f:
        f.write(performance_report)


if __name__ == "__main__":
    main()
