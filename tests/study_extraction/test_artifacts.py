import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from perla_extract.study_extraction.artifacts import (
    write_json_atomic,
    write_json_exclusive,
)


def test_concurrent_json_writers_leave_one_complete_artifact(tmp_path):
    """Shared caches may receive concurrent writes but never partial JSON."""

    path = tmp_path / "result.json"
    values = [{"writer": index, "values": list(range(100))} for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda value: write_json_atomic(path, value), values))

    assert json.loads(path.read_text(encoding="utf-8")) in values
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_serialization_cleans_up_temporary_file(tmp_path):
    with pytest.raises(TypeError):
        write_json_atomic(tmp_path / "result.json", object())

    assert list(tmp_path.iterdir()) == []


def test_exclusive_json_writer_elects_one_complete_winner(tmp_path):
    """Immutable review paths must never overwrite an earlier winner."""

    path = tmp_path / "revision.json"
    values = [{"writer": index} for index in range(8)]

    def publish(value):
        try:
            write_json_exclusive(path, value)
            return "committed"
        except FileExistsError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(publish, values))

    assert outcomes.count("committed") == 1
    assert json.loads(path.read_text(encoding="utf-8")) in values
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_writer_retries_transient_replace_contention(tmp_path, monkeypatch):
    """Windows may briefly deny concurrent replacement of the same target."""

    replace = Path.replace
    attempts = 0

    def transient_contention(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("target is being replaced")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", transient_contention)
    path = tmp_path / "result.json"

    write_json_atomic(path, {"complete": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"complete": True}
    assert attempts == 3
