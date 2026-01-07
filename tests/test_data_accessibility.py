"""Test that data files are accessible when package is installed."""

import importlib.resources
import os


def test_data_dir_exists():
    """Test that the data directory exists."""
    data_dir = importlib.resources.files("perovscribe").joinpath("data")
    assert data_dir.is_dir(), "Data directory should exist"


def test_extractions_dir_exists():
    """Test that the extractions directory exists."""
    data_dir = importlib.resources.files("perovscribe").joinpath("data")
    extractions_dir = data_dir / "extractions"
    assert extractions_dir.is_dir(), "Extractions directory should exist"


def test_ground_truth_test_dir_exists():
    """Test that the ground_truth/test directory exists."""
    data_dir = importlib.resources.files("perovscribe").joinpath("data")
    ground_truth_dir = data_dir / "ground_truth" / "test"
    assert ground_truth_dir.is_dir(), "Ground truth test directory should exist"
    # Verify it contains files
    files = os.listdir(ground_truth_dir)
    assert len(files) > 0, "Ground truth test directory should contain files"


def test_experts_dir_exists():
    """Test that the humans/Consensus directory exists."""
    data_dir = importlib.resources.files("perovscribe").joinpath("data")
    experts_dir = data_dir / "extractions" / "humans" / "Consensus"
    assert experts_dir.is_dir(), "Experts (humans/Consensus) directory should exist"
    # Verify it contains files
    files = os.listdir(experts_dir)
    assert len(files) > 0, "Experts directory should contain JSON files"


def test_all_extraction_dirs_exist():
    """Test that all model extraction directories exist."""
    data_dir = importlib.resources.files("perovscribe").joinpath("data")
    extractions_dir = data_dir / "extractions"
    
    # These are the directories listed in pyproject.toml
    expected_dirs = [
        "claude-opus-4-1-20250805",
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "gpt-4.1-2025-04-14",
        "gpt-4o-2024-08-06",
        "gpt-5-2025-08-07",
        "gpt-5-mini-2025-08-07",
    ]
    
    for dir_name in expected_dirs:
        dir_path = extractions_dir / dir_name
        assert dir_path.is_dir(), f"Extraction directory {dir_name} should exist"
        # Verify each directory contains files
        files = os.listdir(dir_path)
        assert len(files) > 0, f"Extraction directory {dir_name} should contain files"
