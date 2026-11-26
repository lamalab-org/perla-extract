"""The onboarding example follows the same persisted correction path as the UI."""

import copy

import pytest

from review_workbench.demo import PAPER_ID, SOURCE_TEXT, create_demo
from review_workbench.review_storage import StaleRevisionError


def test_demo_owner_correction_persists_and_stale_retry_is_rejected(tmp_path):
    application = create_demo(tmp_path)
    before = application.store.load_bundle("dev", PAPER_ID)
    value = copy.deepcopy(before["ground_truth"]["individual_devices"][0])
    value["family_id"] = "alternative"
    payload = {
        "action": "replace",
        "path": "/individual_devices/0",
        "value": value,
        "base_revision": before["revision"],
        "evidence": [{"block_id": "demo-main-1", "quote": SOURCE_TEXT}],
        "note": "Corrected the owner from the invented source.",
    }
    saved = application.mutate("dev", PAPER_ID, payload, "local-reviewer")
    loaded = application.store.load_bundle("dev", PAPER_ID)
    assert loaded["ground_truth"]["individual_devices"][0]["family_id"] == "alternative"
    assert loaded["revision"] == saved["revision"] == before["revision"] + 1
    assert loaded["events"][-1]["kind"] == "mutation"
    assert loaded["seed_extraction"]["individual_devices"][0]["family_id"] == "control"
    assert application.pdf_path(PAPER_ID, "main", "dev").is_file()
    with pytest.raises(StaleRevisionError):
        application.mutate("dev", PAPER_ID, payload, "local-reviewer")


def test_simple_ab_demo_compares_the_original_with_its_corrected_truth(tmp_path):
    from review_workbench.demo import SIMPLE_AB_ID, create_simple_ab_demo

    application = create_simple_ab_demo(tmp_path)
    view = application.simple_comparisons.open(SIMPLE_AB_ID, "local-reviewer")
    texts = {
        label: " ".join(
            segment["text"]
            for section in candidate["prose"]["sections"]
            for paragraph in section["paragraphs"]
            for segment in paragraph["segments"]
        )
        for label, candidate in view["candidates"].items()
    }
    assert sorted("PCE of 20%" in text for text in texts.values()) == [False, True]
    assert sorted("PCE of 21%" in text for text in texts.values()) == [False, True]
    assert all(text.startswith("Example et al.") for text in texts.values())
