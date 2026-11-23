"""Exercise campaign interruption, accounting and shared comparisons offline."""

import json
from dataclasses import replace

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction import experiment
from perla_extract.study_extraction.experiment_plan import CampaignBudget
from perla_extract.study_extraction.experiment_state import (
    CampaignSpending,
    campaign_lock,
)
from tests.study_extraction.test_development_dataset import (
    fixture_dataset as fixture_dataset,
)
from tests.study_extraction.test_experiment import setup as setup


def test_three_named_arms_share_baseline_and_resume_without_provider_work(setup):
    path, calls, _ = setup
    text = (
        path.read_text()
        .replace("[control]", "[arms.astra]")
        .replace("[treatment]", "[arms.terra]")
    )
    path.write_text(
        'baseline = "astra"\n'
        + text
        + "\n[arms.luna]\nclaim_recall_passes = 3\nmax_model_calls = 4\n"
    )
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 6  # Two repeats, three arms. Astra is not extracted twice.
    output = path.parent / "results"
    comparison = json.loads((output / "comparison.json").read_text())
    assert len(comparison["runs"]) == 6
    assert set(comparison["comparisons"]) == {
        "terra-vs-astra/primary",
        "luna-vs-astra/primary",
    }
    assert comparison["spending"]["known_run_cost_usd"] == 6
    assert len({r["run_id"] for r in comparison["runs"]}) == 6
    resumed = CliRunner().invoke(experiment.main, [str(path), "--resume"])
    assert resumed.exit_code == 0, resumed.output
    assert len(calls) == 6
    assert (
        json.loads((output / "comparison.json").read_text())["spending"][
            "known_run_cost_usd"
        ]
        == 6
    )


def test_full_roster_preflight_precedes_first_paid_attempt(setup, monkeypatch):
    path, calls, extract = setup
    preflights = []

    def observe(config):
        if config.dry_run:
            preflights.append(config)
        else:
            assert len(preflights) == 2
        return extract(config)

    monkeypatch.setattr(experiment, "run_extraction", observe)
    experiment.execute(experiment.prepare(path))
    assert len(calls) == 4


def test_infeasible_last_preflight_prevents_all_paid_attempts(setup, monkeypatch):
    path, calls, extract = setup

    def infeasible(config):
        report = extract(config)
        if config.dry_run and config.claim_recall_passes == 2:
            report["budget"]["planned_calls_fit"] = False
        return report

    monkeypatch.setattr(experiment, "run_extraction", infeasible)
    with pytest.raises(ValueError, match="Preflight failed"):
        experiment.execute(experiment.prepare(path))
    assert calls == []
    assert not (path.parent / "results/spending.json").exists()


def test_killed_attempt_keeps_reservation_and_is_not_redispatched(setup, monkeypatch):
    path, calls, extract = setup
    path.write_text(path.read_text() + "\n[budget]\nmax_cost_usd = 10\n")
    path.write_text(
        path.read_text()
        .replace("[control]", "[control]\nreservation_usd = 2")
        .replace("[treatment]", "[treatment]\nreservation_usd = 2")
    )

    def killed(config):
        if not config.dry_run:
            calls.append(config)
            raise KeyboardInterrupt()
        return extract(config)

    monkeypatch.setattr(experiment, "run_extraction", killed)
    with pytest.raises(KeyboardInterrupt):
        experiment.execute(experiment.prepare(path))
    record = json.loads((path.parent / "results/spending.json").read_text())
    assert len(record["attempts"]) == 1
    entry = next(iter(record["attempts"].values()))
    assert entry["status"] == "reserved" and not entry["cost_complete"]
    monkeypatch.setattr(experiment, "run_extraction", extract)
    experiment.execute(experiment.prepare(path, resume=True))
    assert len(calls) == 4  # One uncertain attempt, three new attempts.
    summary = json.loads((path.parent / "results/accounting.json").read_text())
    assert summary["known_run_cost_usd"] == 3
    assert summary["committed_usd"] == 5
    assert summary["unresolved_attempts"] == 1
    experiment.execute(experiment.prepare(path, resume=True))
    assert len(calls) == 4


def test_failed_sealed_attempt_is_not_retried_on_resume(setup, monkeypatch):
    path, calls, extract = setup
    monkeypatch.setattr(
        experiment, "run_extraction", lambda c: extract(c, fail=not c.dry_run)
    )
    experiment.execute(experiment.prepare(path))
    assert len(calls) == 4
    experiment.execute(experiment.prepare(path, resume=True))
    assert len(calls) == 4
    assert (
        json.loads((path.parent / "results/accounting.json").read_text())[
            "known_run_cost_usd"
        ]
        == 4
    )


def test_changed_method_or_cache_is_rejected_before_resume(setup):
    path, calls, _ = setup
    experiment.execute(experiment.prepare(path))
    cache = next((path.parent / "results/preflight").rglob("document.json"))
    cache.write_text("{}")
    with pytest.raises(ValueError, match="Input changed"):
        experiment.execute(experiment.prepare(path, resume=True))
    assert len(calls) == 4


def test_budget_holds_unknown_costs_and_stops_before_next_dispatch(setup, monkeypatch):
    path, calls, extract = setup
    path.write_text(
        path.read_text()
        .replace("[control]", "[control]\nreservation_usd = 2")
        .replace("[treatment]", "[treatment]\nreservation_usd = 2")
        + "\n[budget]\nmax_cost_usd = 3\n"
    )

    def unknown(config):
        report = extract(config)
        if not config.dry_run:
            report["budget"]["cost_tracking_complete"] = False
            # Update the sealed synthetic report just as a provider report with unknown usage.
            from perla_extract.study_extraction.run_artifacts import write_run_manifest

            directory = config.output_dir
            (directory / "report.json").write_text(json.dumps(report))
            (directory / "run_manifest.json").unlink()
            write_run_manifest(directory)
        return report

    monkeypatch.setattr(experiment, "run_extraction", unknown)
    result = experiment.execute(experiment.prepare(path))
    assert len(calls) == 1
    assert result["quality"] is None
    assert (
        "Campaign admission stopped"
        in (path.parent / "results/errors.json").read_text()
    )
    summary = json.loads((path.parent / "results/accounting.json").read_text())
    assert summary["committed_usd"] == 2
    assert summary["known_run_cost_usd"] == 1
    assert summary["unresolved_attempts"] == 1
    experiment.execute(experiment.prepare(path, resume=True))
    assert len(calls) == 1


def test_observed_overrun_and_prior_holds_stop_admission(tmp_path):
    ledger = CampaignSpending(
        tmp_path,
        "frozen-plan",
        CampaignBudget(
            max_cost_usd=5, prior_known_cost_usd=1, prior_unknown_reservation_usd=2
        ),
    )
    ledger.reserve("one", 2)
    ledger.record.attempts["one"].known_cost_usd = 4
    ledger.record.attempts["one"].cost_complete = True
    ledger.save()
    from perla_extract.study_extraction.experiment_state import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        ledger.reserve("two", 0.1)
    assert ledger.committed() == 7
    assert len(ledger.record.attempts) == 1


def test_lock_rejects_second_writer_and_releases_after_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with campaign_lock(tmp_path):
            with pytest.raises(ValueError, match="Another process"):
                with campaign_lock(tmp_path):
                    pytest.fail("Second writer acquired campaign")
            raise RuntimeError("Simulated interruption")
    with campaign_lock(tmp_path):
        pass


def test_dataset_repeat_subset_has_primary_first_and_three_unique_arms(
    fixture_dataset, tmp_path
):
    dataset, sources, _ = fixture_dataset
    value = json.loads(dataset.read_text())
    value["papers"][0]["repeat_check"] = True
    dataset.write_text(json.dumps(value))
    path = tmp_path / "study.toml"
    path.write_text(f"""output = "results"
dataset = {json.dumps(str(dataset))}
sources = {json.dumps(str(sources))}
repeat_subset = true
baseline = "astra"
[arms.astra.stages.default]
model = "openai/astra"
[arms.terra.stages.default]
model = "openai/terra"
[arms.luna.stages.default]
model = "openai/luna"
""")
    plan = experiment.prepare(path)
    assert len(plan.attempts) == 6
    assert len(plan.repeat_papers) == 1
    assert [a.repeat for a in plan.ordered_attempts()] == [1, 1, 1, 2, 2, 2]
    assert len({a.config.model_cache_dir for a in plan.attempts}) == 6


def test_method_tokenizer_is_relative_to_its_own_file(setup):
    from perla_extract.study_extraction.experiment_plan import (
        ExperimentSettings,
        _arm_settings,
    )

    path, _, _ = setup
    methods = path.parent / "methods"
    methods.mkdir()
    (methods / "method.toml").write_text(
        '[stages.default]\nmodel = "openai/local"\ntokenizer = "tokenizer.json"\n'
    )
    settings = ExperimentSettings.model_validate(
        {
            "output": "results",
            "config": "methods/method.toml",
            "control": {},
            "treatment": {"use_refinement": True},
            "papers": [{"pdf": "paper.pdf", "reference": "reference"}],
        }
    )
    config = _arm_settings(settings, "control", path.parent)
    assert config["deployments"]["default"].tokenizer == str(methods / "tokenizer.json")


def test_production_parser_extractor_scorer_and_repeatability_report(
    setup, monkeypatch
):
    """Only the provider response is simulated; all local pipeline stages run."""
    import pymupdf

    from perla_extract.study_extraction.client import ModelClient
    from perla_extract.study_extraction.models import PaperMetadata, StudyExtraction
    from perla_extract.study_extraction.run_artifacts import file_digest
    from perla_extract.study_extraction.workflow import run_extraction

    path, _, _ = setup
    pdf = path.parent / "paper with spaces.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text(
            (72, 72),
            "The control solar cell reached a PCE of 20% and a fill factor of 80%.",
        )
        document.save(pdf)
    manifest_path = path.parent / "example/truth/dev/10.0000--synthetic/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_manifest"]["source_sha256"] = [file_digest(pdf)]
    manifest_path.write_text(json.dumps(manifest))
    text = path.read_text().replace(
        "use_claim_ledger = true", "use_claim_ledger = false"
    )
    text = text.replace(
        "[control]\nclaim_recall_passes = 1",
        '[arms.astra.stages.default]\nmodel = "openai/synthetic-astra"',
    )
    text = text.replace(
        "[treatment]\nclaim_recall_passes = 2",
        '[arms.terra.stages.default]\nmodel = "openai/synthetic-terra"',
    )
    path.write_text('baseline = "astra"\n' + text)
    calls = []

    def live(self, body, failure):
        calls.append(body["model"])
        empty = StudyExtraction(
            paper=PaperMetadata(title=None, doi=None),
            device_families=[],
            individual_devices=[],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[],
        )
        return empty.model_dump(mode="json"), {
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
            "cost": 0.01,
        }

    monkeypatch.setattr(ModelClient, "_live", live)
    monkeypatch.setattr(experiment, "run_extraction", run_extraction)
    # Add the fixed subset to this one-paper synthetic plan. Dataset role/pin
    # validation is exercised separately; this tests the execution/report boundary.
    plan = experiment.prepare(path)
    plan = replace(
        plan,
        settings=plan.settings.model_copy(update={"repeats": 1}),
        repeat_papers=plan.study.paper_ids,
    )
    result = experiment.execute(plan)
    assert len(calls) == 4
    assert set(result["comparisons"]) == {
        "terra-vs-astra/primary",
        "terra-vs-astra/repeatability",
    }
    assert (
        len(
            json.loads(
                (
                    plan.output / "comparisons/terra-vs-astra/primary/comparison.json"
                ).read_text()
            )["runs"]
        )
        == 2
    )
    assert (
        len(
            json.loads(
                (
                    plan.output
                    / "comparisons/terra-vs-astra/repeatability/comparison.json"
                ).read_text()
            )["runs"]
        )
        == 4
    )
    assert len(result["runs"]) == 4
    assert result["spending"]["known_run_cost_usd"] == 0.04
    assert all(r["reference_agreement_available"] for r in result["runs"])


def test_optional_stage_ablation_compares_enabled_settings_only(setup):
    path, _, _ = setup
    text = path.read_text().replace(
        "use_claim_ledger = true", "use_claim_ledger = false"
    )
    text = text.replace(
        "[control]\nclaim_recall_passes = 1", "[control]\nuse_refinement = false"
    )
    text = text.replace(
        "[treatment]\nclaim_recall_passes = 2", "[treatment]\nuse_refinement = true"
    )
    path.write_text(text)
    plan = experiment.prepare(path)
    contrast = plan.study.factors["deployments.refine.model"]
    assert contrast.control is None
    assert contrast.treatment == "openai/synthetic"
    assert experiment.execute(plan)["quality_status"] == "ready"


def test_missing_explicit_credential_stops_before_parsing_and_can_resume(
    setup, monkeypatch
):
    path, calls, _ = setup
    key = "PERLA_EXPERIMENT_SYNTHETIC_KEY"
    monkeypatch.delenv(key, raising=False)
    path.write_text(
        path.read_text().replace(
            'model = "openai/synthetic"',
            f'model = "openai/synthetic"\napi_key_env = "{key}"',
        )
    )
    with pytest.raises(
        ValueError, match=f"Missing credential environment variable: {key}"
    ):
        experiment.execute(experiment.prepare(path))
    assert calls == []
    assert not (path.parent / "results/preflight").exists()
    assert not (path.parent / "results/spending.json").exists()
    monkeypatch.setenv(key, "synthetic-not-a-secret")
    result = experiment.execute(experiment.prepare(path, resume=True))
    assert len(calls) == 4
    assert result["quality_status"] == "ready"
