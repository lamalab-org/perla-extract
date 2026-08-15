from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "review_app"


def test_ui_enforces_inventory_before_candidates():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    assert "Blind device census" in html
    assert "submit-audit" in html
    assert "hasAudit()" in javascript
    assert "Model candidates are now visible" in javascript


def test_ui_covers_every_rich_record_collection():
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    for collection in (
        "device_families", "individual_devices", "performance_observations",
        "population_statistics", "stability_tests", "equivalence_groups",
    ):
        assert collection in javascript


def test_ui_does_not_reintroduce_flat_cell_editor():
    source = (APP / "app.js").read_text(encoding="utf-8")
    assert "cellCorrection" not in source
    assert "quantity_mentions" not in source


def test_each_collection_uses_its_own_identity_field():
    source = (APP / "app.js").read_text(encoding="utf-8")
    for field in (
        "family_id", "device_id", "observation_id", "population_id", "test_id",
        "equivalence_id",
    ):
        assert field in source


def test_ui_tracks_record_review_and_avoids_prompt_based_creation():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    assert "new-record-kind" in html
    assert "record-decisions" in source
    assert "needs_correction" in source
    assert "record_key" in source
    assert "window.prompt" not in source
