from review_workbench.prepare import prepare


def test_prepare_bundles_only_runtime_and_rich_schema(tmp_path):
    output = prepare(tmp_path / "deployment")
    assert (output / "api/index.py").exists()
    assert (output / "review_workbench/study_review.py").exists()
    assert (output / "src/perla_extract/study_extraction/models.py").exists()
    assert not (output / "src/perla_extract/data").exists()
    assert not (output / "review_workbench/review_collaboration.py").exists()
    assert (output / "review_workbench/review_storage.py").exists()
    assert "class BlobReviewStateStorage" in (output / "api/index.py").read_text(
        encoding="utf-8"
    )
