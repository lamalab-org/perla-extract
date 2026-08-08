from pathlib import Path

from perla_extract.ground_truth import (
    eligible_ground_truth_files,
    exclusion_reasons,
    load_review_metadata,
    paper_metadata,
)


GROUND_TRUTH = (
    Path(__file__).parents[1] / "src" / "perla_extract" / "data" / "ground_truth"
)


def test_review_metadata_covers_all_ground_truth_papers():
    manifest = load_review_metadata(GROUND_TRUTH / "test")
    paper_ids = {
        path.stem
        for split in ("dev", "test")
        for path in (GROUND_TRUTH / split).glob("*.json")
    }

    assert set(manifest["papers"]) == paper_ids


def test_article_type_exclusions_do_not_exclude_tandem_research():
    manifest = load_review_metadata(GROUND_TRUTH / "test")

    review = paper_metadata(manifest, "10.1002--solr.202300438")
    tandem = paper_metadata(manifest, "10.1126--science.adf0194")
    commentary = paper_metadata(manifest, "10.1038--s41560-022-01061-2")

    assert exclusion_reasons(review) == ["article_type:review"]
    assert exclusion_reasons(tandem) == []
    assert exclusion_reasons(commentary) == ["article_type:news_and_views"]


def test_eligibility_partition_excludes_review_metadata():
    included, excluded = eligible_ground_truth_files(GROUND_TRUTH / "test")
    excluded_ids = {path.stem for path, _ in excluded}

    assert "10.1002--solr.202300438" in excluded_ids
    assert "10.1038--s41560-022-01102-w" not in excluded_ids
    assert len(included) + len(excluded) == 20


def test_tandem_device_is_not_in_science_ground_truth():
    import json

    path = GROUND_TRUTH / "test" / "10.1126--science.adf0194.json"
    truth = json.loads(path.read_text(encoding="utf-8"))

    assert len(truth["cells"]) == 1
    assert truth["cells"][0]["pce"]["value"] == 20.3
    assert "tandem" not in (truth["cells"][0].get("additional_notes") or "").lower()
