from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "review_app"


def test_ui_enforces_inventory_before_candidates():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    assert "Blind device census" in html
    assert "submit-audit" in html
    assert "hasAudit()" in javascript
    assert "Model candidates are now visible" in javascript
    assert "renderQualityArtifacts()" in javascript


def test_ui_covers_every_rich_record_collection():
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    for collection in (
        "device_families",
        "individual_devices",
        "performance_observations",
        "population_statistics",
        "stability_tests",
        "identity_links",
    ):
        assert collection in javascript


def test_ui_does_not_reintroduce_flat_cell_editor():
    source = (APP / "app.js").read_text(encoding="utf-8")
    assert "cellCorrection" not in source
    assert "quantity_mentions" not in source


def test_backend_is_the_single_source_for_record_identity_fields():
    source = (APP / "app.js").read_text(encoding="utf-8")
    backend = (APP.parent / "study_review.py").read_text(encoding="utf-8")
    for field in (
        "family_id",
        "device_id",
        "observation_id",
        "population_id",
        "test_id",
        "link_id",
    ):
        assert field in backend
        assert field not in source
    assert "record_identifiers" in source


def test_ui_tracks_record_review_and_avoids_prompt_based_creation():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    assert "new-record-kind" in html
    assert "record-decisions" in source
    assert "needs_correction" in source
    assert "record_key" in source
    assert "window.prompt" not in source


def test_ui_builds_untrusted_content_with_dom_nodes():
    source = (APP / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source
    assert "replaceChildren" in source


def test_only_admins_can_download_an_adjudicated_pr_bundle():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    assert "Download PR bundle" in html
    assert "ground-truth-export" in source
    assert 'state.user.role === "admin"' in source
    assert 'finalEvent?.details?.stage === "adjudication"' in source
