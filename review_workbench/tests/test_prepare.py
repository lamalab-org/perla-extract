import os
import subprocess
import sys

from review_workbench.prepare import prepare


def test_prepare_bundles_only_runtime_and_rich_schema(tmp_path):
    output = prepare(tmp_path / "deployment")
    assert (output / "api/index.py").exists()
    assert (output / "review_workbench/study_review.py").exists()
    assert (output / "review_workbench/ground_truth_export.py").exists()
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
