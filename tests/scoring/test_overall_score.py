import pytest
import numpy as np
from omegaconf import OmegaConf
from perovscribe.overall_score import (
    DetailedScore,
    ParameterToleranceResult,
    DeviceLevelScore,
    OverallScore,
    check_essential_parameters_within_tolerance,
)


@pytest.fixture
def sample_config():
    """Create a sample configuration for testing"""
    config = {"tolerances": {"pce": 0.05, "jsc": 0.05, "voc": 0.05, "ff": 0.05}}
    return OmegaConf.create(config)


@pytest.fixture
def sample_cell_pair():
    """Create a sample pair of truth and predicted cells"""
    truth_cell = {
        "additional_notes": "test_device",
        "pce": {"value": 20.0},
        "jsc": {"value": 22.5},
        "voc": {"value": 1.1},
        "ff": {"value": 0.75},
        "cell_stack": ["FTO", "TiO2", "Perovskite", "Spiro-OMeTAD", "Au"],
        "layers": [{"name": "FTO", "thickness": 40}, {"name": "TiO2", "thickness": 50}],
    }

    pred_cell = {
        "additional_notes": "test_device",
        "pce": {"value": 19.5},
        "jsc": {"value": 22.0},
        "voc": {"value": 1.05},
        "ff": {"value": 0.73},
        "cell_stack": ["FTO", "TiO2", "Perovskite", "Spiro-OMeTAD", "Au"],
        "layers": [{"name": "FTO", "thickness": 40}, {"name": "TiO2", "thickness": 45}],
    }

    return truth_cell, pred_cell


class TestDetailedScore:
    def test_calculate(self, sample_cell_pair):
        truth_cell, pred_cell = sample_cell_pair
        score = DetailedScore.calculate(truth_cell, pred_cell)

        assert score.pce_mae == pytest.approx(0.5)
        assert score.jsc_mae == pytest.approx(0.5)
        assert score.voc_mae == pytest.approx(0.05)
        assert score.ff_mae == pytest.approx(0.02)

    def test_calculate_missing_values(self):
        """Test handling of non-extracted parameters"""
        truth_cell = {
            "pce": {"value": 20.0},
            "jsc": {"value": 22.5},
            "voc": {"value": 1.1},
            "ff": {"value": 0.75},
        }

        # Case 1: Parameter completely missing from prediction
        pred_cell_missing = {
            "pce": {"value": 19.5},
            "voc": {"value": 1.05},
            "ff": {"value": 0.73},
        }
        score = DetailedScore.calculate(truth_cell, pred_cell_missing)
        assert not np.isnan(score.pce_mae)
        assert np.isnan(score.jsc_mae)
        assert not np.isnan(score.voc_mae)
        assert not np.isnan(score.ff_mae)

        # Case 2: Parameter exists but value is None
        pred_cell_none = {
            "pce": {"value": 19.5},
            "jsc": {"value": None},
            "voc": {"value": 1.05},
            "ff": {"value": 0.73},
        }
        score = DetailedScore.calculate(truth_cell, pred_cell_none)
        assert not np.isnan(score.pce_mae)
        assert np.isnan(score.jsc_mae)
        assert not np.isnan(score.voc_mae)
        assert not np.isnan(score.ff_mae)

        # Case 3: Parameter exists but missing value key
        pred_cell_no_value = {
            "pce": {"value": 19.5},
            "jsc": {},
            "voc": {"value": 1.05},
            "ff": {"value": 0.73},
        }
        score = DetailedScore.calculate(truth_cell, pred_cell_no_value)
        assert not np.isnan(score.pce_mae)
        assert np.isnan(score.jsc_mae)
        assert not np.isnan(score.voc_mae)
        assert not np.isnan(score.ff_mae)

    def test_aggregate(self):
        scores = [
            DetailedScore(pce_mae=0.5, jsc_mae=0.3, voc_mae=0.02, ff_mae=0.01),
            DetailedScore(pce_mae=0.3, jsc_mae=0.4, voc_mae=0.03, ff_mae=0.02),
        ]

        aggregated = DetailedScore.aggregate(scores)
        assert aggregated.pce_mae == pytest.approx(0.4)
        assert aggregated.jsc_mae == pytest.approx(0.35)
        assert aggregated.voc_mae == pytest.approx(0.025)
        assert aggregated.ff_mae == pytest.approx(0.015)

    def test_aggregate_with_nan(self):
        scores = [
            DetailedScore(pce_mae=0.5, jsc_mae=np.nan, voc_mae=0.02, ff_mae=0.01),
            DetailedScore(pce_mae=0.3, jsc_mae=0.4, voc_mae=np.nan, ff_mae=0.02),
            DetailedScore(pce_mae=np.nan, jsc_mae=0.2, voc_mae=0.04, ff_mae=np.nan),
        ]

        aggregated = DetailedScore.aggregate(scores)

        assert aggregated.pce_mae == pytest.approx(0.4)

        assert aggregated.jsc_mae == pytest.approx(0.3)

        assert aggregated.voc_mae == pytest.approx(0.03)

        assert aggregated.ff_mae == pytest.approx(0.015)


class TestDeviceLevelScore:
    @pytest.fixture
    def sample_device_score(self, sample_cell_pair):
        truth_cell, pred_cell = sample_cell_pair
        parameter_scores = {
            "pce": ParameterToleranceResult(True, 0.025, 20.0, 19.5, 0.05),
            "jsc": ParameterToleranceResult(True, 0.022, 22.5, 22.0, 0.05),
            "stack": ParameterToleranceResult(True, None, None, None),
        }

        return DeviceLevelScore(
            device_id="test_device",
            deepdiff_overall={"type_changes": {}},
            deepdiff_stack={},
            deepdiff_layers={
                "values_changed": {
                    "root[0]['thickness']": {"old_value": 50, "new_value": 45}
                }
            },
            parameter_scores=parameter_scores,
            detailed_score=DetailedScore(
                pce_mae=0.5, jsc_mae=0.5, voc_mae=0.05, ff_mae=0.02
            ),
        )

    def test_fraction_parameters_within_tolerance(self, sample_device_score):
        assert sample_device_score.fraction_parameters_within_tolerance == 1.0

    def test_parameters_is_within_tolerance(self, sample_device_score):
        assert sample_device_score.parameters_is_within_tolerance


class TestOverallScore:
    @pytest.fixture
    def sample_overall_score(self, sample_cell_pair):
        truth_cell, pred_cell = sample_cell_pair
        cfg = OmegaConf.create({"tolerances": {"pce": 0.5, "jsc": 0.5}})

        return OverallScore.calculate([truth_cell], [pred_cell], cfg)

    def test_calculate(self, sample_overall_score):
        assert sample_overall_score.num_devices_found == 1
        assert sample_overall_score.num_devices_matched == 1
        assert sample_overall_score.recall == 1.0
        assert len(sample_overall_score.device_scores) == 1

    def test_avg_essential_parameters_score(self, sample_overall_score):
        print(sample_overall_score)
        assert sample_overall_score.avg_essential_parameters_score == pytest.approx(1.0)

    def test_sum_essential_parameters_score(self, sample_overall_score):
        assert sample_overall_score.sum_essential_parameters_score == pytest.approx(1.0)


def test_check_essential_parameters_within_tolerance(sample_cell_pair, sample_config):
    truth_cell, pred_cell = sample_cell_pair
    results = check_essential_parameters_within_tolerance(
        truth_cell, pred_cell, sample_config.tolerances
    )
    print(results)
    assert set(results.keys()) == {"pce", "jsc", "voc", "ff", "stack"}

    pce_result = results["pce"]
    assert pce_result.within_tolerance
    assert pce_result.relative_error == pytest.approx(0.025)
    assert pce_result.truth_value == 20.0
    assert pce_result.pred_value == 19.5

    # Check stack comparison
    stack_result = results["stack"]
    assert stack_result.within_tolerance
    assert stack_result.relative_error is None


def test_check_essential_parameters_missing_values(sample_config):
    truth_cell = {"pce": {"value": 20.0}, "cell_stack": ["FTO"]}
    pred_cell = {"pce": {"value": None}, "cell_stack": ["FTO"]}

    results = check_essential_parameters_within_tolerance(
        truth_cell, pred_cell, sample_config.tolerances
    )

    assert not results["pce"].within_tolerance
    assert results["pce"].relative_error is None


def test_check_essential_parameters_error_handling():
    # Test with invalid input
    truth_cell = {"pce": None}
    pred_cell = {"pce": {"value": 20.0}}
    tolerances = OmegaConf.create({"pce": 0.05})

    results = check_essential_parameters_within_tolerance(
        truth_cell, pred_cell, tolerances
    )
    assert isinstance(results, dict)
