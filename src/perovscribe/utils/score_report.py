from typing import List, Dict, Any
from omegaconf import DictConfig
import numpy as np
from perovscribe.overall_score import OverallScore


def get_layer_differences(
    truth_layer: Dict[str, Any], pred_layer: Dict[str, Any], field_labels: DictConfig
) -> Dict[str, Dict[str, Any]]:
    """
    Compare individual layers between truth and prediction for specific fields.

    Args:
        truth_layer (Dict[str, Any]): Ground truth layer dictionary
        pred_layer (Dict[str, Any]): Predicted layer dictionary
        field_labels (DictConfig): Configuration specifying fields to compare and their labels

    Returns:
        Dict[str, Dict[str, Any]]: Dictionary of differences found between the layers
    """
    differences = {}
    for field, label in field_labels.items():
        truth_value = truth_layer.get(field)
        pred_value = pred_layer.get(field)

        if truth_value != pred_value:
            differences[field] = {
                "label": label,
                "truth": truth_value,
                "extracted": pred_value,
            }

    return differences


def format_layer_difference(
    layer_index: int, differences: Dict[str, Dict[str, Any]]
) -> List[str]:
    """
    Format layer differences into readable strings.

    Args:
        layer_index (int): Index of the layer being compared
        differences (Dict[str, Dict[str, Any]]): Dictionary of differences found in the layer

    Returns:
        List[str]: List of formatted strings describing the differences
    """
    lines = [f"      Layer {layer_index} Differences:"]

    for field, diff in differences.items():
        truth_val = "None" if diff["truth"] is None else diff["truth"]
        extracted_val = "None" if diff["extracted"] is None else diff["extracted"]

        lines.extend(
            [
                f"        {diff['label']}:",
                f"          Groundtruth: {truth_val}",
                f"          Extracted: {extracted_val}",
            ]
        )

    return lines


def analyze_layer_differences(
    truth_cell: Dict[str, Any], pred_cell: Dict[str, Any], layer_fields: DictConfig
) -> List[Dict[str, Any]]:
    """
    Analyze differences between layers in truth and predicted cells.

    Args:
        truth_cell (Dict[str, Any]): Ground truth cell dictionary
        pred_cell (Dict[str, Any]): Predicted cell dictionary
        layer_fields (DictConfig): Configuration specifying which fields to compare

    Returns:
        List[Dict[str, Any]]: List of dictionaries containing layer differences
    """
    layer_differences = []

    for i, (truth_layer, pred_layer) in enumerate(
        zip(truth_cell["layers"], pred_cell["layers"])
    ):
        differences = get_layer_differences(truth_layer, pred_layer, layer_fields)
        if differences:
            layer_differences.append({"index": i, "differences": differences})

    return layer_differences


def format_parameter_validation_result(
    param: str, result: Any, param_config: Any
) -> List[str]:
    """
    Format the validation results for a single parameter.

    Args:
        param (str): Name of the parameter being validated
        result (Any): ParameterToleranceResult object containing validation results
        param_config (Any): Configuration for the parameter including units

    Returns:
        List[str]: List of formatted strings describing the validation results
    """
    lines = []
    unit_str = f" {param_config.units}" if param_config.units else ""

    if result.truth_value is not None and result.pred_value is not None:
        lines.extend(
            [
                f"    {param.upper()}:",
                f"      Ground Truth: {result.truth_value}{unit_str}",
                f"      Extracted: {result.pred_value}{unit_str}",
                f"      Relative Error: {result.relative_error*100:.2f}%",
                f"      Within Tolerance ({result.tolerance*100:.1f}%): {result.within_tolerance}",
            ]
        )
    else:
        lines.extend(
            [
                f"    {param.upper()}:",
                f"      Ground Truth: {'N/A' if result.truth_value is None else result.truth_value}{unit_str}",
                f"      Extracted: {'N/A' if result.pred_value is None else result.pred_value}{unit_str}",
                f"      Match: {result.within_tolerance}",
            ]
        )
    return lines


def generate_detailed_report(
    overall_score: OverallScore,
    cfg: DictConfig,
    truth_cells: List[Dict[str, Any]],
    extracted_cells: List[Dict[str, Any]],
) -> str:
    """
    Generate a detailed report comparing truth and extracted cells.

    Args:
        overall_score (OverallScore): OverallScore object containing evaluation metrics
        cfg (DictConfig): Configuration object
        truth_cells (List[Dict[str, Any]]): List of ground truth cells
        extracted_cells (List[Dict[str, Any]]): List of extracted cells (pre-matched with truth_cells)

    Returns:
        str: Formatted string containing the detailed report
    """

    def format_device_section(device_score, cell, prefix=""):
        lines = []
        lines.append(f"{prefix}ID/Notes: {device_score.device_id}")

        for param, param_config in cfg.parameters.items():
            value = cell.get(param, {}).get("value", "N/A")
            unit_str = f" {param_config.units}" if param_config.units else ""
            lines.append(f"{prefix}{param.upper()}: {value}{unit_str}")

        lines.append(f"{prefix}Stack: {', '.join(cell['cell_stack'])}")
        lines.append(
            f"{prefix}Composition: {cell.get('perovskite_composition', 'N/A')}"
        )
        return lines

    report = [
        "DETAILED MATCHING ANALYSIS REPORT",
        "=" * 80,
        "",
        "1. DEVICE COUNTS",
        "-" * 80,
        f"Total devices: {len(truth_cells)}",
        f"Successfully extracted devices: {len(extracted_cells)}",
        "",
        "2. DEVICE ANALYSIS",
        "-" * 80,
        "",
    ]

    for idx, ((truth_cell, pred_cell), device_score) in enumerate(
        zip(zip(truth_cells, extracted_cells), overall_score.device_scores), 1
    ):
        report.append(f"Device {idx}:")
        report.append("  Ground Truth Device:")
        report.extend(format_device_section(device_score, truth_cell, "    "))
        report.append("  Extracted Device:")
        report.extend(format_device_section(device_score, pred_cell, "    "))

        report.append("  Parameter Comparisons:")
        for param, param_config in cfg.parameters.items():
            if param in device_score.parameter_scores:
                report.extend(
                    format_parameter_validation_result(
                        param, device_score.parameter_scores[param], param_config
                    )
                )

        stack_result = device_score.parameter_scores.get("stack")
        if stack_result:
            report.append("    Stack Match:")
            report.append(f"      Within Tolerance: {stack_result.within_tolerance}")

        layer_differences = analyze_layer_differences(
            truth_cell, pred_cell, cfg.layer_fields
        )
        report.append(f"    Layer differences: {len(layer_differences)}")
        if layer_differences:
            report.extend(
                [
                    line
                    for diff in layer_differences
                    for line in format_layer_difference(
                        diff["index"], diff["differences"]
                    )
                ]
            )

        report.append(
            f"    Overall Match Score: {device_score.fraction_parameters_within_tolerance:.3f}"
        )
        report.append("")

    return "\n".join(report)


def generate_performance_report(overall_score: OverallScore, cfg: DictConfig) -> str:
    """
    Generate a summary report of overall extraction performance.

    Args:
        overall_score (OverallScore): OverallScore object containing evaluation metrics
        cfg (DictConfig): Configuration object

    Returns:
        str: Formatted string containing the performance report
    """
    lines = [
        "=== Overall Extraction Performance ===",
        f"Total Devices: {overall_score.num_devices_found}",
        f"Successfully Extracted: {overall_score.num_devices_matched}",
        f"Extraction Rate: {overall_score.recall:.3f}",
        f"Average Parameters Match Score: {overall_score.avg_essential_parameters_score:.3f}",
        f"Sum Parameters Match Score: {overall_score.sum_essential_parameters_score:.3f}",
        "\n=== Aggregate Parameter Extraction Accuracy ===",
    ]

    for param in cfg.parameters:
        mae = getattr(overall_score.detailed_aggregate, f"{param}_mae")
        lines.append(
            f"{param.upper()} MAE: {mae:.3f}"
            if not np.isnan(mae)
            else f"{param.upper()} MAE: N/A"
        )

    lines.append("\n=== Individual Device Analysis ===")

    for device in overall_score.device_scores:
        device_lines = [
            f"\nDevice: {device.device_id}",
            f"Overall Match Score: {device.fraction_parameters_within_tolerance:.3f}",
            "Parameter Results:",
        ]

        for param, result in device.parameter_scores.items():
            if param != "stack":
                if result.relative_error is not None:
                    device_lines.append(
                        f"  {param.upper()}: {result.relative_error*100:.2f}% error, "
                        f"Within {result.tolerance*100:.1f}% tolerance: {result.within_tolerance}"
                    )
                else:
                    device_lines.append(
                        f"  {param.upper()}: Match: {result.within_tolerance}"
                    )

        stack_result = device.parameter_scores.get("stack")
        if stack_result:
            device_lines.append(f"  Stack Match: {stack_result.within_tolerance}")

        device_lines.extend(
            [
                f"Stack differences: {len(device.deepdiff_stack.get('values_changed', {}))}",
                f"Layer differences: {len(device.deepdiff_layers.get('values_changed', {}))}",
            ]
        )

        for param in cfg.parameters:
            mae = getattr(device.detailed_score, f"{param}_mae")
            device_lines.append(
                f"{param.upper()} MAE: {mae:.3f}"
                if not np.isnan(mae)
                else f"{param.upper()} MAE: N/A"
            )

        lines.extend(device_lines)

    return "\n".join(lines)
