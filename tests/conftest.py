import pytest
import os
import json

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture()
def get_nat_comm_7139_file():
    return os.path.join(THIS_DIR, "test_files", "nat_comm_7139.pdf")


@pytest.fixture()
def truth():
    return json.load(
        open(
            os.path.join(
                THIS_DIR,
                "test_files",
                "truth_test_file_10.1016--j.orgel.2017.01.022.json",
            )
        )
    )


@pytest.fixture()
def best_extraction():
    return json.load(
        open(
            os.path.join(
                THIS_DIR,
                "test_files",
                "extraction_best_test_file_10.1016--j.orgel.2017.01.022.json",
            )
        )
    )


@pytest.fixture()
def mid_extraction():
    return json.load(
        open(
            os.path.join(
                THIS_DIR,
                "test_files",
                "extraction_mid_test_file_10.1016--j.orgel.2017.01.022.json",
            )
        )
    )


@pytest.fixture()
def worst_extraction():
    return json.load(
        open(
            os.path.join(
                THIS_DIR,
                "test_files",
                "extraction_worst_test_file_10.1016--j.orgel.2017.01.022.json",
            )
        )
    )


@pytest.fixture()
def matching_1():
    return json.load(open(os.path.join(THIS_DIR, "test_files", "matching_1.json")))


@pytest.fixture()
def matching_2():
    return json.load(open(os.path.join(THIS_DIR, "test_files", "matching_2.json")))


@pytest.fixture()
def normalize_input():
    return json.load(
        open(os.path.join(THIS_DIR, "test_files", "test_normalize_input.json"))
    )


@pytest.fixture()
def normalize_output():
    return json.load(
        open(os.path.join(THIS_DIR, "test_files", "test_normalize_output.json"))
    )
