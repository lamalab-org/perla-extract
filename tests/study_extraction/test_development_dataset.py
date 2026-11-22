"""Keep reusable source roles and reference identity intact before inference."""

import hashlib
import json
import shutil
from collections import Counter

import pytest
from click.testing import CliRunner

from examples.benchmarks.development import DATASET, main, verify_dataset
from perla_extract.study_extraction.reference_io import reference_content_digest


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


@pytest.fixture
def fixture_dataset(tmp_path):
    public = json.loads(DATASET.read_text())
    original_release = (DATASET.parent / public["reference_release"]).resolve()
    release = json.loads(original_release.read_text())
    row = next(
        r for r in public["papers"] if r["paper_id"] == "10.1002--adfm.201904856"
    )
    entry = next(r for r in release["papers"] if r["paper_id"] == row["paper_id"])
    reference = tmp_path / entry["path"]
    reference.mkdir(parents=True)
    for name in ("ground_truth.json", "manifest.json"):
        shutil.copyfile(
            original_release.parent / entry["path"] / name, reference / name
        )
    sources = tmp_path / "sources"
    sources.mkdir()
    for role in ("pdf", "supplement"):
        path = sources / row[role]["file"]
        path.write_bytes(("distinct test source: " + role).encode())
        row[role]["sha256"] = _sha(path)
    manifest = json.loads((reference / "manifest.json").read_text())
    manifest["source_manifest"]["source_sha256"] = [
        row[r]["sha256"] for r in ("pdf", "supplement")
    ]
    _save(reference / "manifest.json", manifest)
    entry["manifest_sha256"] = reference_content_digest(manifest)
    release.update(release="test-fixture", paper_count=1, papers=[entry])
    _save(tmp_path / "release.json", release)
    public.update(
        reference_release="release.json",
        reference_release_sha256=_sha(tmp_path / "release.json"),
        papers=[row],
    )
    dataset = tmp_path / "papers.json"
    _save(dataset, public)
    return dataset, sources, reference


def test_committed_roster_pins_all_references_and_repeat_subset():
    value, papers = verify_dataset(DATASET)
    assert len(papers) == 30
    assert sum(1 + (r["supplement"] is not None) for r in value["papers"]) == 57
    assert sum(r["source_pages"] for r in value["papers"]) == 1143
    assert Counter(
        r["length_stratum"] for r in value["papers"] if r["repeat_check"]
    ) == {"short": 3, "medium": 3, "long": 4}


def test_source_roles_cannot_be_swapped(fixture_dataset):
    dataset, sources, _ = fixture_dataset
    value, _ = verify_dataset(dataset, sources)
    row = value["papers"][0]
    article, si = (sources / row[r]["file"] for r in ("pdf", "supplement"))
    original_article, original_si = article.read_bytes(), si.read_bytes()
    article.write_bytes(original_si)
    si.write_bytes(original_article)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_dataset(dataset, sources)


def test_reference_changes_cannot_silently_enter_the_dataset(fixture_dataset):
    dataset, sources, reference = fixture_dataset
    path = reference / "ground_truth.json"
    truth = json.loads(path.read_text())
    truth["performance_observations"][0]["metrics"][0]["raw_value"] = "999"
    _save(path, truth)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_dataset(dataset, sources)


def test_reference_json_formatting_preserves_its_content_hash(fixture_dataset):
    dataset, sources, reference = fixture_dataset
    path = reference / "ground_truth.json"
    path.write_bytes(path.read_bytes() + b"\n")
    _, papers = verify_dataset(dataset, sources)
    assert len(papers) == 1


def test_export_exposes_article_si_and_reference_paths(fixture_dataset, tmp_path):
    dataset, sources, reference = fixture_dataset
    output = tmp_path / "export"
    result = CliRunner().invoke(
        main,
        ["--dataset", str(dataset), "--sources", str(sources), "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    papers = json.loads((output / "papers.json").read_text())
    assert papers[0]["reference"] == str(reference)
    assert papers[0]["pdf"].endswith("adfm.201904856.pdf")
    assert papers[0]["supplement"].endswith("adfm.201904856-SI.pdf")
    cohort = json.loads((output / "cohort.json").read_text())
    assert cohort["papers"] == [{"paper_id": papers[0]["paper_id"]}]
    assert cohort["max_model_calls_per_paper"] == 1


def test_missing_si_prevents_any_export(fixture_dataset, tmp_path):
    dataset, sources, _ = fixture_dataset
    row = json.loads(dataset.read_text())["papers"][0]
    (sources / row["supplement"]["file"]).unlink()
    output = tmp_path / "export"
    result = CliRunner().invoke(
        main,
        ["--dataset", str(dataset), "--sources", str(sources), "--output", str(output)],
    )
    assert result.exit_code != 0
    assert "Missing file" in result.output
    assert not output.exists()


def test_repeat_subset_export_names_the_derived_cohort(fixture_dataset, tmp_path):
    dataset, sources, _ = fixture_dataset
    value = json.loads(dataset.read_text())
    value["papers"][0]["repeat_check"] = True
    _save(dataset, value)
    output = tmp_path / "repeat-export"
    result = CliRunner().invoke(
        main,
        [
            "--dataset",
            str(dataset),
            "--sources",
            str(sources),
            "--repeat-subset",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    cohort = json.loads((output / "cohort.json").read_text())
    assert cohort["name"].endswith("-repeatability")
    assert len(cohort["papers"]) == 1


def test_repeated_reference_is_rejected(fixture_dataset):
    dataset, sources, _ = fixture_dataset
    value = json.loads(dataset.read_text())
    value["papers"] *= 2
    _save(dataset, value)
    with pytest.raises(ValueError, match="roster"):
        verify_dataset(dataset, sources)
