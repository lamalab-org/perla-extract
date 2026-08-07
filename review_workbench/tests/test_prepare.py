from review_workbench.prepare import prepare


def test_prepare_creates_minimal_deployment_project(tmp_path):
    output = prepare(tmp_path / "deployment")

    assert (output / "api" / "index.py").exists()
    assert (output / "review_workbench" / "review_app" / "index.html").exists()
    assert (output / "src" / "perla_extract" / "data" / "ground_truth" / "test").is_dir()
    dependencies = (output / "pyproject.toml").read_text()
    assert "PyMuPDF" in dependencies
    assert "vercel>=0.8" in dependencies
    assert "litellm" not in dependencies
