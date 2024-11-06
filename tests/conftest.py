import pytest
import os

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture()
def get_nat_comm_7139_file():
    return os.path.join(THIS_DIR, "test_files", "nat_comm_7139.pdf")
