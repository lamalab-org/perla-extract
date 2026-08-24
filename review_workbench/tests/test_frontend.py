from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "review_app"


def test_ui_allows_record_review_before_the_census():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    assert "Paper and figure census" in html
    assert "submit-audit" in html
    assert "hasAudit()" in javascript
    assert "renderReviewQueue();" in javascript
    assert "Review extracted records at any time" in javascript
    assert "model-assisted record review" not in javascript
    assert "renderQualityArtifacts()" in javascript


def test_inventory_measures_the_main_text_figure_gap_without_source_checkboxes():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert "searched-main" not in html
    assert "searched-supplement" not in html
    assert "Main-text figure gap" in html
    assert "Figure-only atomic values" in html
    assert "not explicitly reported in the caption, running text, or a table" in html
    assert "not each panel" in html
    assert "do not infer or digitize uncertain values" in html
    assert "main_text_figure_census" in javascript
    assert "review_scope_sources: state.bundle.sources" in javascript
    assert "This legacy inventory did not record a main-text figure census" in javascript


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


def test_pipeline_statuses_are_explained_as_review_priorities():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "How to review records and fix missing or extra records" in html
    assert "These are priorities, not correctness claims" in html
    assert "Passed automated checks" in source
    assert "It still requires human comparison with the source" in source
    assert "You marked this for correction" in source
    assert "Added during the second extraction read" in source
    assert "Revised during the second extraction read" in source
    assert "a change is a review priority, not a correctness claim" in source
    assert "correction required" not in source
    assert "changed by quality pass" not in source
    assert "added by quality pass" not in source


def test_revision_conflicts_use_reviewer_language_and_offer_recovery():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "Load latest saved version" in html
    assert "Discard this edit and load latest" in html
    assert "review_revision_conflict" in source
    assert "reloadLatestPaper" in source
    assert "HTTPStatus.CONFLICT" in server
    assert "This paper changed in another review session" in server
    assert "stale revision" not in html
    assert "stale revision" not in source


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


def test_record_review_prioritizes_the_current_record_over_device_context():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "Review queue" in html
    assert "record-status-filter" in html
    assert "record-kind-filter" in html
    assert "Current review target" in source
    assert "Related device context (expand if needed)" in source
    assert "relatedContext" in source
    assert "focusCitation" in source
    assert 'key === "v"' in source
    assert 'key === "u"' in source
    assert 'key === "c"' in source


def test_inventory_defines_counts_without_hiding_records():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "What counts as a family, device, or measurement?" in html
    assert "One shared recipe or architecture variant" in source
    assert "One particular measured specimen" in source
    assert "Multiple measurements of the same cell are not additional devices" in source
    assert 'id="census-status"' in html
    assert "You can inspect and correct Records now" in html
    assert "Edit saved census" in html
    assert 'tab === "completeness" && !hasAudit()' in source
    assert '["records", "completeness"].includes(tab) && !hasAudit()' not in source


def test_stability_review_shows_every_atomic_value_before_related_context():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "renderStabilityRecord" in source
    assert "Test-wide conditions" in source
    assert "Checkpoint-specific conditions" in source
    assert 'reportedValueGroup(entry, "Outcomes"' in source
    assert "Show value in paper" in source
    assert "recordJsonPath" in source
    assert source.index("renderReviewTarget(entry)") < source.index("renderDeviceContext(entry)", source.index("renderReviewTarget(entry)"))
    assert "All fields match source" in html
    assert "All fields match source  V" in source
    assert "Cannot establish from source  U" in source
    assert "Your decision applies to the complete current record" in source
    assert "Verify  V" not in source


def test_show_in_paper_has_direct_lookup_visible_location_and_race_protection():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "citation-location" in html
    assert "pdf-highlights" in html
    assert "citation-match-label" in html
    assert "/api/evidence-block/" in source
    assert "scrollIntoView" in source
    assert "preview.decode()" in source
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
    assert "preview.decode()" in source
    assert 'responseType: "pdfPage"' in source
    assert "loadPdfPage" in source
    assert "requestWithRetry" in source
    assert "AbortController" in source
    assert 'id="retry-pdf"' in html
    assert "URL.createObjectURL" in source
    assert server.count("private, max-age=3600, immutable") == 2


def test_record_count_corrections_are_explicit_and_reference_guarded():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    backend = (APP.parent / "study_review.py").read_text(encoding="utf-8")

    assert "Copy as missing record" in source
    assert "Remove extra record" in html
    assert "linked measurements must be reassigned or removed first" in html
    assert "/api/study-schema" in source
    assert "draftFromSchema" in source
    assert "recordReferences" in source
    assert "reviewReferencedRecord" in source
    assert "Add missing record" in source
    assert "Save field correction" in source
    assert "other records refer to it" in backend
    assert 'dependency.hidden = intent !== "remove"' in source
    assert '$("remove-record").hidden = intent !== "remove"' in source
    assert '$("cancel-record").addEventListener("click", () => $("record-dialog").close())' in source
    assert "linkedRecordSummary" in source
    assert "pointing to something that no longer exists" in source


def test_startup_defers_schema_and_shows_real_loading_states():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert 'id="paper-load-status"' in html
    assert 'id="retry-startup"' in html
    assert "Loading review workspace…" in source
    assert "Your saved reviews are unchanged" in source
    assert "paperCacheKey" in source
    assert source.rstrip().endswith("await startApp();")
    assert not source.rstrip().endswith("await loadStudySchema();")
    assert 'if (!state.studySchema)' in source


def test_correction_and_removal_dialogs_open_without_saving_a_decision_first():
    source = (APP / "app.js").read_text(encoding="utf-8")
    correction = source[source.index("function beginCorrection"):source.index("function beginRemoval")]
    removal = source[source.index("function beginRemoval"):source.index("function copyMissingRecord")]

    assert "openRecord" in correction
    assert "submitDecision" not in correction
    assert "openRecord" in removal
    assert "submitDecision" not in removal


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
    assert "Download adjudicated PR bundle" in html
    assert "ground-truth-export" in source
    assert 'state.user.role === "admin"' in source
    assert 'finalEvent?.details?.stage === "adjudication"' in source


def test_reviewers_can_inspect_and_download_their_persisted_annotations():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "My edits &amp; undo" in html
    assert "My annotations and edits" in html
    assert "Download my annotations" in html
    assert "/api/reviewer-progress/" in source
    assert "current_record_decisions" in source
    assert 'text: "Before"' in source
    assert 'text: "After"' in source
    assert "Inspect exact saved event" in source
    assert "Undo this saved edit" in html
    assert "undoAnnotation" in source
    assert "/api/mutation-undos/" in source
    assert "undoable_event_ids" in source
    assert "Nothing is erased from history" in html
    assert "No saved field correction is currently safe to undo" in source
    assert "change this decision" in source
    assert "Edit saved census" in source
    assert 'application.reviewer_progress(parts[2], user["id"])' in server
    assert 'application.undo_mutation(' in server


def test_file_actions_are_direct_responsive_and_show_progress():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    styles = (APP / "styles.css").read_text(encoding="utf-8")

    assert 'id="download-review-workbook"' in html
    assert 'id="open-files"' in html
    assert 'id="files-dialog"' in html
    assert "download-menu" not in html
    assert "Preparing…" in source
    assert 'button.setAttribute("aria-busy", "true")' in source
    assert "setFileStatus" in source
    assert "@media (max-width:650px)" in styles
    assert "main { display:block" in styles


def test_reviewers_can_download_source_pdfs_and_current_study_json():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")

    assert "Files for review" in html
    assert "Download study JSON" in html
    assert "Download main paper" in html
    assert "Download supporting information" in html
    assert "bundle.ground_truth" in source
    assert ".study.json" in source
    assert "/api/pdf/" in source
    assert 'sources.includes("supplement")' in source
    assert "Local changes are not saved in the workbench" in source


def test_reviewers_can_round_trip_a_device_or_paper_excel_review():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert "Download editable Excel" in html
    assert "Upload completed workbook" in html
    assert "Download Excel for this device" in source
    assert "downloadReviewWorkbook" in source
    assert "uploadReviewWorkbook" in source
    assert "/api/review-workbook/" in source
    assert "application.review_workbook(" in server
    assert "application.import_review_workbook(" in server
    assert "one validated revision" in source
