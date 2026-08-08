from review_workbench.prepare import prepare


def test_prepare_creates_minimal_deployment_project(tmp_path):
    output = prepare(tmp_path / "deployment")

    assert (output / "api" / "index.py").exists()
    assert (output / "review_workbench" / "review_app" / "index.html").exists()
    assert (output / "src" / "perla_extract" / "pydantic_model_reduced.py").exists()
    assert (output / "src" / "perla_extract" / "data" / "ground_truth" / "test").is_dir()
    dependencies = (output / "pyproject.toml").read_text()
    assert "PyMuPDF" in dependencies
    assert "vercel>=0.8" in dependencies
    assert "pydantic>=2.0" in dependencies
    assert "litellm" not in dependencies


def test_prepare_preserves_existing_vercel_project_link(tmp_path):
    output = tmp_path / "deployment"
    project_link = output / ".vercel" / "project.json"
    project_link.parent.mkdir(parents=True)
    project_link.write_text('{"projectName":"review-workbench"}')
    environment = output / ".vercel" / ".env.production.local"
    environment.write_text("SECRET=kept-locally")

    prepare(output)

    assert project_link.read_text() == '{"projectName":"review-workbench"}'
    assert environment.read_text() == "SECRET=kept-locally"
