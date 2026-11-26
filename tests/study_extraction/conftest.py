"""Scientific contracts run offline; prepare optional parser weights separately."""

import os
import socket

import pytest

# Set before test-module imports: LiteLLM may otherwise fetch its cost table.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def no_network_in_scientific_tests(monkeypatch):
    """A missing mock must fail before a provider or download can be contacted."""

    def blocked(*args, **kwargs):
        raise AssertionError("Scientific tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
