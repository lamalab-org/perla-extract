import os
import subprocess
import sys

import pytest

from review_workbench.prepare import prepare


def test_prepared_deployment_storage_sdk_contract(tmp_path):
    """Exercise the real SDK import and call signature, without contacting storage.

    Most review tests use a fake Blob service. This test instead installs/runs with
    the deployment dependencies in CI, catching SDK API changes at cold startup.
    """

    pytest.importorskip("vercel")
    output = prepare(tmp_path / "deployment")
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import inspect
from api.index import BlobStore
store = BlobStore(token="contract-test-only")
assert store.client is not None
inspect.signature(store.client.put).bind(
    "test.json", b"{}", access="private", content_type="application/json",
    add_random_suffix=False, overwrite=False, cache_control_max_age=60,
)
""",
        ],
        cwd=output,
        env={
            **os.environ,
            "BLOB_READ_WRITE_TOKEN": "",
            "REVIEW_BACKUP_READ_WRITE_TOKEN": "",
            "PYTHONPATH": os.pathsep.join((str(output), str(output / "src"))),
        },
        check=True,
    )


def test_prepare_bundles_only_runtime_and_rich_schema(tmp_path):
    output = prepare(tmp_path / "deployment")
    assert (output / "api/index.py").exists()
    assert (output / "review_workbench/study_review.py").exists()
    assert (output / "review_workbench/spreadsheet_review.py").exists()
    assert (output / "review_workbench/ground_truth_export.py").exists()
    assert (output / "review_workbench/feedback_export.py").exists()
    assert (output / "src/perla_extract/study_extraction/models.py").exists()
    assert (output / "src/perla_extract/study_extraction/enrichment.py").exists()
    assert (output / "src/perla_extract/study_extraction/validation.py").exists()
    assert not (output / "src/perla_extract/data").exists()
    assert not (output / "review_workbench/review_collaboration.py").exists()
    assert (output / "review_workbench/review_storage.py").exists()
    assert "class BlobReviewStateStorage" in (output / "api/index.py").read_text(
        encoding="utf-8"
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from perla_extract.study_extraction.artifacts import "
                "write_json_atomic; from review_workbench.server import "
                "ReviewApplication"
            ),
        ],
        cwd=output,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(output), str(output / "src"))),
        },
        check=True,
    )
