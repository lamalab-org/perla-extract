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


def test_ui_shows_reported_composition_beside_enrichment_proposals():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert 'name="enrichment"' in html
    assert 'name="targeted_repair"' in html
    assert "Source-reported composition" in javascript
    assert "Proposed site interpretation" in javascript
    assert "composition_results" in javascript
    assert "result.status" in javascript


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


def test_record_review_is_a_device_context_queue():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "Review queue" in html
    assert "record-status-filter" in html
    assert "record-kind-filter" in html
    assert "Device context" in source
    assert "relatedContext" in source
    assert "focusCitation" in source
    assert 'key === "v"' in source
    assert 'key === "u"' in source
    assert 'key === "c"' in source


def test_show_in_paper_has_direct_lookup_visible_location_and_race_protection():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "citation-location" in html
    assert "pdf-highlights" in html
    assert "citation-match-label" in html
    assert "/api/evidence-block/" in source
    assert "scrollIntoView" in source
    assert "image.decode()" in source
    assert 'event.key === "Enter"' in source
    assert "requestId !== state.pdfRequest" in source
    assert "application.evidence_block(parts[2], parts[3], parts[4])" in server


def test_pdf_source_switch_is_visible_cached_and_waits_for_the_new_page():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "Supporting information (SI)" in html
    assert 'id="pdf-message"' in html
    assert 'setAttribute("aria-busy", "true")' in source
    assert "Large SI files can take a few seconds" in source
    assert "image.decode()" in source
    assert server.count("private, max-age=3600, immutable") == 2


def test_record_count_corrections_are_explicit_and_reference_guarded():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    backend = (APP.parent / "study_review.py").read_text(encoding="utf-8")

    assert "Copy as missing record" in source
    assert "Remove extra record" in html
    assert "Removal is blocked until linked measurements" in html
    assert "/api/study-schema" in source
    assert "draftFromSchema" in source
    assert "recordReferences" in source
    assert "reviewReferencedRecord" in source
    assert "Add missing record" in source
    assert "Save field correction" in source
    assert "other records refer to it" in backend


def test_corrections_default_to_fields_and_existing_evidence():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "structured-editor" in html
    assert "Raw record JSON" in html
    assert "Fields" in html
    assert "renderStructuredEditor" in source
    assert "setRecordEditorMode" in source
    assert "recordFieldPath" in source
    assert "citation?.block_id" in source
    assert "MATERIAL_FORMS" in source
    assert "schema_compatibility" in source
    assert "fields added since import still require review or regeneration" in source


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


def test_reviewers_can_inspect_and_download_their_persisted_annotations():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "My annotations" in html
    assert "Download my annotations" in html
    assert "/api/reviewer-progress/" in source
    assert "current_record_decisions" in source
    assert 'text: "Before"' in source
    assert 'text: "After"' in source
    assert "Inspect exact saved event" in source
    assert 'application.reviewer_progress(parts[2], user["id"])' in server
