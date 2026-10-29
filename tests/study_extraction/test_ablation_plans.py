"""Check experiment isolation and real CLI compatibility without provider calls."""

import json
import sys

import pytest
from click import ClickException
from click.testing import CliRunner

from examples.ablations.prepare import ROOT, Study, check_inputs, digest, prepare
from examples.ablations.references import audit_release
from examples.ablations.references import main as audit_cli
from examples.scoring.run import seed_study
from perla_extract.study_extraction import cli
from perla_extract.study_extraction.evaluation_cli import reference_content_digest
from perla_extract.study_extraction.models import study_schema_sha256

STUDIES = sorted((ROOT / "examples/ablations").glob("[0-9][0-9]-*.json"))


@pytest.fixture
def cohort(tmp_path):
    (tmp_path / "main.pdf").write_bytes(b"synthetic main")
    (tmp_path / "si.pdf").write_bytes(b"synthetic SI")
    truth = tmp_path / "truth"
    truth.mkdir()
    payload = seed_study().model_dump(mode="json")
    (truth / "ground_truth.json").write_text(json.dumps(payload), encoding="utf-8")
    (truth / "manifest.json").write_text(
        json.dumps(
            {
                "artifact_format_version": 4,
                "study_schema_sha256": study_schema_sha256(),
                "paper_id": "paper-a",
                "split": "dev",
                "evidence_version": 1,
                "evidence_document_sha256": "a" * 64,
                "source_manifest": {
                    "source_sha256": [
                        digest(tmp_path / name) for name in ("main.pdf", "si.pdf")
                    ]
                },
                "files": {"ground_truth.json": reference_content_digest(payload)},
                "review": {
                    "uncertain_record_keys": [],
                    "completed_stage_names": ["adjudication"],
                },
                "validation": {"status": "verified", "issue_count": 0},
            }
        ),
        encoding="utf-8",
    )
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


def test_descriptor_rules_are_frozen_for_all_arms_and_never_sent_to_models(
    cohort, tmp_path
):
    rules = tmp_path / "equivalences.json"
    # A multi-paper list can contain rules for references outside this tiny cohort.
    payload = [
        {
            "reference_sha256": "0" * 64,
            "record_kind": "population_statistics",
            "reference_label": "control",
            "alternative_labels": ["untreated"],
            "reason": "Synthetic checked cohort equivalence.",
        }
    ]
    rules.write_text(json.dumps(payload))
    plan = make_plan(
        STUDIES[0], cohort, tmp_path / "plan", descriptor_equivalences=rules
    )
    assert plan["input_sha256"][str(rules)] == digest(rules)
    assert plan["scorer"]["config"]["descriptor_equivalences"] == payload
    for job in plan["jobs"]:
        assert str(rules) not in job["extract"]
        assert "--descriptor-equivalences" in job["score"]
        assert job["score"][job["score"].index("--descriptor-equivalences") + 1] == str(
            rules
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
        from dataclasses import fields

        from perla_extract.study_extraction.workflow import (
            ExtractionConfig,
            _run_configuration,
        )

        config_options = {
            field.name: options[field.name] for field in fields(ExtractionConfig)
        }
        config_options["reasoning_effort"] = cli.normalize_reasoning_effort(
            options["reasoning_effort"]
        )
        configuration = _run_configuration(
            ExtractionConfig(**config_options),
            "single",
            [
                {"source_sha256": digest(tmp_path / name)}
                for name in ("main.pdf", "si.pdf")
            ],
            [],
        )
        for field, expected in job["expected_configuration"].items():
            assert configuration[field] == expected, field
        assert str(tmp_path / "truth") not in json.dumps(job["expected_configuration"])
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
        assert any(job["score_output"] in command for command in plan["aggregates"])
        assert job["score"][job["score"].index("--output") + 1] == job["score_output"]
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
    # The example has invented text, not a publisher PDF. Bind this test's
    # synthetic byte fixture explicitly; never change a published reference here.
    manifest_path = example / "truth/dev/10.0000--synthetic/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest"]["source_sha256"] = [digest(pdf)]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
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


@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "missing_si",
        "identity",
        "unfinalized",
        "validation",
        "truth",
        "legacy",
    ],
)
def test_preflight_refuses_untrusted_or_wrong_inputs_without_creating_plan(
    cohort, tmp_path, damage
):
    path = tmp_path / "truth/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if damage == "source":
        (tmp_path / "main.pdf").write_bytes(b"different paper")
    elif damage == "missing_si":
        payload = json.loads(cohort.read_text(encoding="utf-8"))
        payload[0]["supplement"] = None
        cohort.write_text(json.dumps(payload), encoding="utf-8")
    elif damage == "identity":
        manifest["paper_id"] = "wrong-paper"
    elif damage == "unfinalized":
        manifest["review"]["completed_stage_names"] = []
    elif damage == "validation":
        manifest["validation"]["issue_count"] = 1
    elif damage == "truth":
        manifest["files"]["ground_truth.json"] = "f" * 64
    else:
        manifest["artifact_format_version"] = 3
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, ClickException)):
        make_plan(STUDIES[0], cohort, tmp_path / "plan")
    assert not (tmp_path / "plan").exists()


def test_plan_pins_release_and_reports_group_coverage(cohort, tmp_path):
    manifest = json.loads(
        (tmp_path / "truth/manifest.json").read_text(encoding="utf-8")
    )
    release = {
        "release": "test-release",
        "split": "dev",
        "paper_count": 1,
        "papers": [
            {
                "paper_id": "paper-a",
                "ground_truth_sha256": manifest["files"]["ground_truth.json"],
            }
        ],
    }
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    plan = make_plan(STUDIES[0], cohort, tmp_path / "plan", release_path=release_path)
    assert plan["format_version"] == 3
    assert plan["references"]["paper-a"]["fact_counts"]["performance"] == 1
    assert plan["references"]["paper-a"]["fact_counts"]["stability"] == 0
    assert all("--release" in command for command in plan["aggregates"])
    assert str(release_path) not in plan["jobs"][0]["extract"]
    release["papers"].append({"paper_id": "paper-b", "ground_truth_sha256": "b" * 64})
    release["paper_count"] = 2
    release_path.write_text(json.dumps(release), encoding="utf-8")
    with pytest.raises(ValueError, match="roster mismatch"):
        make_plan(
            STUDIES[0], cohort, tmp_path / "incomplete", release_path=release_path
        )


def test_reference_audit_distinguishes_integrity_from_review_coverage(cohort, tmp_path):
    manifest_path = tmp_path / "truth/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    release = {
        "release": "test",
        "split": "dev",
        "paper_count": 1,
        "papers": [
            {
                "paper_id": "paper-a",
                "path": "truth",
                "ground_truth_sha256": manifest["files"]["ground_truth.json"],
                "manifest_sha256": reference_content_digest(manifest),
            }
        ],
    }
    path = tmp_path / "release.json"
    path.write_text(json.dumps(release), encoding="utf-8")
    result = audit_release(path)
    assert result["software_checks"] == "passed"
    assert result["source_documents_rechecked"] is False
    assert result["papers_without_completeness_stage_marker"] == ["paper-a"]
    assert result["papers_with_facts"]["performance"] == 1
    output = tmp_path / "audit.json"
    args = ["--release", str(path), "--output", str(output)]
    assert CliRunner().invoke(audit_cli, args).exit_code == 0
    saved = output.read_bytes()
    assert CliRunner().invoke(audit_cli, args).exit_code != 0
    assert output.read_bytes() == saved
    # An unsupported change to metadata must not silently upgrade the review claim.
    manifest["review"]["completed_stage_names"].append("completeness")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest hash"):
        audit_release(path)
