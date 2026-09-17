"""Check experiment isolation and real CLI compatibility without provider calls."""

import json
import sys

import pytest
from click.testing import CliRunner

from examples.ablations.prepare import ROOT, Study, check_inputs, prepare
from perla_extract.study_extraction import cli

STUDIES = sorted((ROOT / "examples/ablations").glob("[0-9][0-9]-*.json"))


@pytest.fixture
def cohort(tmp_path):
    (tmp_path / "main.pdf").write_bytes(b"synthetic main")
    (tmp_path / "si.pdf").write_bytes(b"synthetic SI")
    truth = tmp_path / "truth"
    truth.mkdir()
    for filename in ("ground_truth.json", "manifest.json"):
        (truth / filename).write_text("{}")
    path = tmp_path / "cohort.json"
    path.write_text(
        json.dumps(
            [
                {
                    "paper_id": "paper-a",
                    "pdf": "main.pdf",
                    "supplement": "si.pdf",
                    "truth": "truth",
                }
            ]
        )
    )
    return path


def make_plan(path, cohort, output, **kwargs):
    return prepare(
        path, cohort, output, "provider/strong", "provider/reader", 5, 2, 0, **kwargs
    )


@pytest.mark.parametrize("study_path", STUDIES, ids=lambda path: path.stem)
def test_isolated_arms_and_real_extraction_cli(
    study_path, cohort, tmp_path, monkeypatch
):
    observed = []
    monkeypatch.setattr(
        cli, "extract_study", lambda **kw: observed.append(kw) or {"status": "complete"}
    )
    plan = make_plan(study_path, cohort, tmp_path / "plan")
    assert len(plan["jobs"]) == 4
    assert len(plan["aggregates"]) == 4  # Never pool stochastic repeats as new papers.
    settings = {}
    for job in plan["jobs"]:
        result = CliRunner().invoke(cli.main, job["extract"][3:])
        assert result.exit_code == 0, result.output
        options = observed[-1]
        assert options["supplement"] == tmp_path / "si.pdf"
        assert "truth" not in options
        assert str(tmp_path / "truth") not in job["extract"]
        assert options["use_enrichment"] and options["use_targeted_repair"]
        settings[job["arm"]] = {
            k: v
            for k, v in options.items()
            if k not in {"output_dir", "model_cache_dir"}
        }
    changed = {
        key
        for key in settings["control"]
        if settings["control"][key] != settings["treatment"][key]
    }
    assert changed == {plan["study"]["factor"]}
    assert (
        len(
            {
                job["extract"][job["extract"].index("--model-cache-dir") + 1]
                for job in plan["jobs"]
            }
        )
        == 4
    )
    for job in plan["jobs"]:
        assert any(job["score"][-2] in command for command in plan["aggregates"])
    with pytest.raises(FileExistsError):
        make_plan(study_path, cohort, tmp_path / "plan")


def test_changed_source_is_rejected_before_any_script_runs(
    cohort, tmp_path, monkeypatch
):
    plan = make_plan(STUDIES[0], cohort, tmp_path / "plan")
    monkeypatch.setattr(
        "examples.ablations.prepare.subprocess.check_output",
        lambda command, **kw: plan["revision"] if command[1] == "rev-parse" else "",
    )
    check_inputs(plan)
    (tmp_path / "si.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Input changed"):
        check_inputs(plan)


def test_multiple_factors_rejected(cohort, tmp_path):
    path = tmp_path / "bad.json"
    payload = Study.model_validate_json(STUDIES[0].read_text()).model_dump()
    payload["arms"]["treatment"]["use_refinement"] = False
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="only in the declared factor"):
        make_plan(path, cohort, tmp_path / "plan")


def test_duplicate_papers_rejected(cohort, tmp_path):
    data = json.loads(cohort.read_text())
    cohort.write_text(json.dumps(data + data))
    with pytest.raises(ValueError, match="distinct papers"):
        make_plan(STUDIES[0], cohort, tmp_path / "plan")


@pytest.mark.skipif(
    sys.platform == "win32", reason="Generated scripts target bash on Linux/macOS"
)
def test_shell_scripts_parse_and_include_all_planned_jobs(cohort, tmp_path):
    import subprocess

    output = tmp_path / "plan with spaces"
    plan = make_plan(STUDIES[0], cohort, output)
    for stage in ("extract", "score"):
        subprocess.run(["bash", "-n", str(output / f"{stage}.sh")], check=True)
        text = (output / f"{stage}.sh").read_text()
        assert "check" in text and 'exit "$failed"' in text
        assert text.count(" || failed=1") == len(plan["jobs"]) + (
            4 if stage == "score" else 0
        )


def test_generated_scoring_commands_accept_review_export_and_aggregate(tmp_path):
    import shutil

    from examples.scoring.run import build_example
    from perla_extract.study_extraction.evaluation_cli import main as score
    from perla_extract.study_extraction.evaluation_dataset_cli import main as aggregate

    example = tmp_path / "example"
    build_example(example)
    pdf = tmp_path / "main.pdf"
    pdf.write_bytes(b"synthetic")
    cohort = tmp_path / "cohort.json"
    cohort.write_text(
        json.dumps(
            [
                {
                    "paper_id": "10.0000--synthetic",
                    "pdf": str(pdf),
                    "supplement": None,
                    "truth": str(example / "truth/dev/10.0000--synthetic"),
                }
            ]
        )
    )
    plan = make_plan(STUDIES[0], cohort, tmp_path / "plan")
    for job in plan["jobs"]:
        run = job["score"][job["score"].index("--prediction") + 1]
        shutil.copytree(example / "predictions/corrected", run)
        result = CliRunner().invoke(score, job["score"][3:])
        assert result.exit_code == 0, result.output
    for command in plan["aggregates"]:
        result = CliRunner().invoke(aggregate, command[3:])
        assert result.exit_code == 0, result.output


@pytest.mark.parametrize("reader", [None, "provider/strong"])
def test_hybrid_requires_explicit_distinct_model(cohort, tmp_path, reader):
    path = ROOT / "examples/ablations/04-cheaper-reader.json"
    with pytest.raises(ValueError, match="explicit, distinct"):
        prepare(path, cohort, tmp_path / "plan", "provider/strong", reader, 5, 1, 0)
