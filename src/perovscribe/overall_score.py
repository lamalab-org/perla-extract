from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from deepdiff import DeepDiff
import numpy as np
from omegaconf import DictConfig


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
        for param in ["pce", "jsc", "voc", "ff"]:
            truth_val = truth_cell.get(param, {}).get("value")
            pred_val = pred_cell.get(param, {}).get("value")

            maes[f"{param}_mae"] = (
                abs(truth_val - pred_val)
                if truth_val is not None and pred_val is not None
                else np.nan
            )

        return cls(**maes)

    @classmethod
    def aggregate(cls, scores: List["DetailedScore"]) -> "DetailedScore":
        aggregated_maes = {}
        for param in ["pce", "jsc", "voc", "ff"]:
            values = [getattr(s, f"{param}_mae") for s in scores]
            aggregated_maes[f"{param}_mae"] = np.nanmean(values)
        return cls(**aggregated_maes)


@dataclass
class ParameterToleranceResult:
    within_tolerance: bool
    error: Optional[float]
    truth_value: Optional[float]
    pred_value: Optional[float]
    tolerance: Optional[float] = None


@dataclass
class DeviceLevelScore:
    device_id: str
    deepdiff_overall: dict
    deepdiff_stack: dict
    deepdiff_layers: dict
    parameter_scores: Dict[str, ParameterToleranceResult]
    detailed_score: DetailedScore

    @property
    def fraction_parameters_within_tolerance(self) -> float:
        valid_scores = [
            score
            for score in self.parameter_scores.values()
            if score.within_tolerance is not None
        ]
        if not valid_scores:
            return 0.0
        return sum(1 for score in valid_scores if score.within_tolerance) / len(
            valid_scores
        )

    @property
    def parameters_is_within_tolerance(self) -> bool:
        return all(result.within_tolerance for result in self.parameter_scores.values())


@dataclass
class OverallScore:
    num_devices_found: int
    num_devices_matched: int
    recall: float
    device_scores: List[DeviceLevelScore]
    detailed_aggregate: DetailedScore

    @property
    def avg_essential_parameters_score(self) -> float:
        return np.mean(
            [d.fraction_parameters_within_tolerance for d in self.device_scores]
        )

    @property
    def sum_essential_parameters_score(self) -> float:
        return sum(d.fraction_parameters_within_tolerance for d in self.device_scores)

    @classmethod
    def calculate(
        cls,
        truth_cells: List[dict],
        pred_cells: List[dict],
        cfg: DictConfig,
    ) -> "OverallScore":
        """
        Calculate scores for pre-matched truth and predicted cells.

        Args:
            truth_cells (List[dict]): List of ground truth cells
            pred_cells (List[dict]): List of predicted cells (in same order as truth_cells)
            cfg (DictConfig): Configuration containing tolerances and other settings

        Returns:
            OverallScore: OverallScore object containing detailed evaluation metrics
        """
        device_scores = []
        detailed_scores = []

        for truth, pred in zip(truth_cells, pred_cells):
            detailed_score = DetailedScore.calculate(truth, pred)
            detailed_scores.append(detailed_score)
            parameter_scores = check_essential_parameters_within_tolerance(
                truth, pred, cfg.tolerances
            )

            device_score = DeviceLevelScore(
                device_id=truth.get("additional_notes", "unknown"),
                deepdiff_overall=DeepDiff(truth, pred, ignore_order=True, ignore_string_case=True, ignore_numeric_type_changes=True),
                deepdiff_stack=DeepDiff(
                    truth["cell_stack"], pred["cell_stack"], ignore_order=False, ignore_string_case=True, ignore_numeric_type_changes=True
                ),
                deepdiff_layers=DeepDiff(
                    truth["layers"], pred["layers"], ignore_order=True,  ignore_string_case=True, ignore_numeric_type_changes=True
                ),
                parameter_scores=parameter_scores,
                detailed_score=detailed_score,
            )
            device_scores.append(device_score)

        recall = len(device_scores) / len(truth_cells) if truth_cells else np.nan
        detailed_aggregate = DetailedScore.aggregate(detailed_scores)

        return cls(
            num_devices_found=len(pred_cells),
            num_devices_matched=len(device_scores),
            recall=recall,
            device_scores=device_scores,
            detailed_aggregate=detailed_aggregate,
        )


def check_essential_parameters_within_tolerance(
    truth: Dict[str, Any],
    pred: Dict[str, Any],
    tolerances: DictConfig,
) -> Dict[str, ParameterToleranceResult]:
    """
    Check if each parameter is within its specified tolerance.

    Args:
        truth (Dict[str, Any]): Dictionary containing ground truth values
        pred (Dict[str, Any]): Dictionary containing predicted values
        tolerances (DictConfig): Configuration containing tolerance values for each parameter

    Returns:
        Dict[str, ParameterToleranceResult]: Dictionary mapping parameter names to ParameterToleranceResult objects
    """
    results = {}

    try:
        for param, tolerance in tolerances.items():
            truth_param = truth.get(param, {}) if truth is not None else {}
            pred_param = pred.get(param, {}) if pred is not None else {}

            truth_val = (
                truth_param.get("value") if isinstance(truth_param, dict) else None
            )
            pred_val = pred_param.get("value") if isinstance(pred_param, dict) else None

            if truth_val is not None and pred_val is not None:
                try:
                    error = abs(truth_val - pred_val)
                    within_tol = error <= tolerance
                except (TypeError, ValueError):
                    error = None
                    within_tol = False
            else:
                error = None
                within_tol = False

            results[param] = ParameterToleranceResult(
                within_tolerance=within_tol,
                error=error,
                truth_value=truth_val,
                pred_value=pred_val,
                tolerance=tolerance,
            )

        if (
            truth is not None
            and pred is not None
            and "cell_stack" in truth
            and "cell_stack" in pred
        ):
            stack_match = truth["cell_stack"] == pred["cell_stack"]
        else:
            stack_match = False

        results["stack"] = ParameterToleranceResult(
            within_tolerance=stack_match,
            relative_error=None,
            truth_value=None,
            pred_value=None,
        )

    except Exception as e:
        print(f"Error in tolerance check: {str(e)}")

    return results


# @hydra.main(version_base=None, config_path="././conf", config_name="config_params")
# def main(cfg: DictConfig, truth_cells: List[dict], extracted_cells: List[dict]) -> None:
#     """
#     Main function to evaluate extraction performance.

#     Args:
#         cfg (DictConfig): Configuration object
#         truth_cells (List[dict]): List of ground truth cells
#         extracted_cells (List[dict]): List of extracted cells
#     """
#     matched_pairs = match_cells(truth_cells, extracted_cells)
#     matched_truth = [pair["truth"] for pair in matched_pairs]
#     matched_extracted = [pair["extraction"] for pair in matched_pairs]
#     scores = OverallScore.calculate(matched_truth, matched_extracted, cfg)
#     print(scores)


# if __name__ == "__main__":
#     main()
