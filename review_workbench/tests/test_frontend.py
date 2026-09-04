from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "review_app"


def test_blinded_comparison_is_a_separate_review_workflow():
    main = (APP / "index.html").read_text(encoding="utf-8")
    html = (APP / "comparison.html").read_text(encoding="utf-8")
    javascript = (APP / "comparison.js").read_text(encoding="utf-8")
    backend = (APP.parent / "expert_comparison.py").read_text(encoding="utf-8")

    assert 'href="/comparison.html"' in main
    assert "one randomly assigned output" in html
    assert "Values and units are separate claims" in html
    assert "Extra records" in html and "Missing records" in html and "Wrong links" in html
    assert "Chemical detail" in html and "Usefulness for NOMAD" in html
    assert "/api/comparison-reviews/" in javascript
    assert "/api/native-comparisons/" in javascript
    assert "/api/native-utility-reviews/" in javascript
    assert "/api/pairwise-comparisons/" in javascript
    assert "/api/pairwise-preference-reviews/" in javascript
    assert "Your accuracy review is locked" in html
    assert "Blinded A/B preference" in html
    assert "Both inadequate" in javascript
    assert "Cannot judge" in javascript
    assert "Factual correctness" in backend
    assert "Coverage and completeness" in backend
    assert "NOMAD readiness" in backend
    assert "Minimum acceptable:" in javascript
    assert "Preference rule:" in javascript
    assert "state.preference.rubrics" in javascript
    assert "How to apply the preference choices" in html
    assert "Neither candidate reaches" in html
    assert "Submit and lock this review" in javascript
    assert "historical_database" not in javascript
    assert "new_extractor" not in javascript


def test_refreshed_review_dataset_is_the_clear_default():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert html.count(
        '<option value="dev">Current review — refreshed extraction</option>'
    ) == 2
    assert html.count(
        '<option value="calibration">Legacy calibration — deprecated</option>'
    ) == 2
    assert 'split: "dev"' in javascript
    assert 'calibration: "Legacy calibration"' in javascript


def test_deployed_authentication_has_a_recoverable_sign_in_flow():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    styles = (APP / "styles.css").read_text(encoding="utf-8")

    assert 'id="auth-gate"' in html
    assert 'id="internal-sign-in"' in html
    assert 'id="use-email-sign-in"' in html
    assert "Forgot your password? Use email recovery" in html
    assert 'id="sign-out"' in html
    assert 'fetch("/api/auth/config")' in javascript
    assert 'fetch("/api/auth/login"' in javascript
    assert 'state.authMode === "internal_or_clerk"' in javascript
    assert "Choose Forgot password in the form" in javascript
    assert 'elevation: "flush"' in javascript
    assert 'colorPrimary: "#176b52"' in javascript
    assert "Your session expired. Sign in again" in javascript
    assert 'localStorage.removeItem(REVIEW_TOKEN_KEY)' in javascript
    assert '"Connection problem"' not in javascript
    assert ".internal-sign-in[hidden]" in styles
    assert "#clerk-sign-in[hidden]" in styles


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
    assert "Mark census reviewed" in html
    assert "Mark inventory reviewed" not in html
    assert "Blind inventory" not in javascript


def test_inventory_measures_the_main_text_figure_gap_without_source_checkboxes():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert "searched-main" not in html
    assert "searched-supplement" not in html
    assert "Main-text subfigures" in html
    assert 'id="add-figure-panel"' in html
    assert 'id="figure-panels"' in html
    assert "absent from running text, captions, and tables" in html
    assert "Do not count curve samples or uncertain visual estimates" in html
    for figure_class in (
        "jv",
        "eqe",
        "population_statistics",
        "stability",
        "characterization",
        "device_structure",
        "other",
    ):
        assert f'{figure_class}:' in javascript
    assert "inset_table" in javascript
    assert "requires_digitization" in javascript
    assert "figureCensusTotals" in javascript
    assert "main_text_figure_census" in javascript
    assert "review_scope_sources: state.bundle.sources" in javascript
    assert "Earlier aggregate census" in javascript


def test_ui_covers_every_rich_record_collection():
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    for collection in (
        "device_families",
        "individual_devices",
        "performance_observations",
        "population_statistics",
        "stability_tests",
    ):
        assert collection in javascript


def test_record_review_layout_responds_to_panel_width_without_overlays():
    styles = (APP / "styles.css").read_text(encoding="utf-8")

    assert ".review-panel { container:review-panel / inline-size; }" in styles
    assert "@container review-panel (max-width:600px)" in styles
    assert ".add-record-menu[open] { grid-column:1 / -1; }" in styles
    assert "position:absolute" not in styles.split(".add-record-menu .record-add", 1)[1].split("}", 1)[0]
    assert ".reported-value { display:grid; }" in styles


def test_laptop_layout_can_reclaim_space_and_focus_each_work_surface():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    styles = (APP / "styles.css").read_text(encoding="utf-8")

    assert 'id="toggle-paper-list"' in html
    assert '>Papers</button>' in html
    assert 'button.textContent = "Papers"' in javascript
    assert 'data-workspace-view="split"' in html
    assert 'data-workspace-view="paper"' in html
    assert 'data-workspace-view="review"' in html
    assert "function setPaperListOpen" in javascript
    assert "if (!state.paperId) setPaperListOpen(true, false);" in javascript
    assert "function setWorkspaceView" in javascript
    assert 'const LAPTOP_LAYOUT = "(max-width: 1400px)"' in javascript
    assert 'setWorkspaceView(window.matchMedia("(max-width: 920px)").matches ? "paper" : "split", false)' in javascript
    assert "main.paper-list-hidden" in styles
    assert '.workspace-grid[data-view="paper"]' in styles
    assert '.workspace-grid[data-view="review"]' in styles


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
    assert "One complete photovoltaic design" in source
    assert "Characterization-only films and partial stacks" in source
    assert "One particular measured specimen" in source
    assert "Multiple measurements of the same cell are not additional devices" in source
    assert "You can inspect and correct Records now" not in html
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
    assert 'id="pdf-current-source"' in html
    assert 'setAttribute("aria-busy", "true")' in source
    assert "Large SI files can take a few seconds" in source
    assert "preview.decode()" in source
    assert 'responseType: "pdfPage"' in source
    assert "loadPdfPage" in source
    assert "requestWithRetry" in source
    assert "AbortController" in source
    assert "pdfDisplayed" in source
    assert "PDF_VIEW_VERSION" in source
    assert '$("pdf-canvas").hidden = !displayed || displayed.paperId !== paperId' in source
    assert "The previous page is still shown." in source
    assert 'id="retry-pdf"' in html
    assert "URL.createObjectURL" in source
    assert server.count("private, max-age=3600, immutable") == 2


def test_record_count_corrections_are_explicit_and_reference_guarded():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    backend = (APP.parent / "study_review.py").read_text(encoding="utf-8")

    assert "Duplicate and edit" in source
    assert "Merge duplicate" in source
    assert "Change record type" in source
    assert "/api/record-merges/" in source
    assert '"record-reclassifications"' in source
    assert "Remove extra record" in html
    assert "A merge moves explicit links automatically" in html
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
    assert "if (await initializeAuthentication())" in source
    assert "await startApp();" in source
    assert not source.rstrip().endswith("await loadStudySchema();")
    assert 'if (!state.studySchema)' in source


def test_failed_navigation_keeps_an_open_paper_and_progress_requires_a_session():
    source = (APP / "app.js").read_text(encoding="utf-8")

    selection = source[source.index("async function selectPaper"):source.index("function renderStudy")]
    progress = source[source.index("async function openReviewerProgress"):source.index("async function downloadReviewerProgress")]
    assert 'if (state.bundle) setStatus(`Could not open ${paperId}' in selection
    assert 'else {\n      $("empty-title").textContent = "Could not open this paper";' in selection
    assert "if (!state.user)" in progress
    assert 'setStatus("Sign in before opening saved review progress.", true);' in progress
    assert progress.index("if (!state.user)") < progress.index('$("annotations-dialog").showModal()')


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
    assert "My review progress" in html
    assert "Current work" in html
    assert "History" in html
    assert "Download activity JSON" in html
    assert "Reset all current progress" in html
    assert "/api/reviewer-progress/" in source
    assert "current_record_decisions" in source
    assert 'text: "Before"' in source
    assert 'text: "After"' in source
    assert "Inspect exact saved event" in source
    assert "Undo this saved edit" in source
    assert "undoAnnotation" in source
    assert "/api/mutation-undos/" in source
    assert "/api/reviewer-resets/" in source
    assert "resetReviewerProgress" in source
    assert "resetPaperProgress" in source
    assert "Continue reviewing records" in source
    assert "sessionStorage.setItem(reviewerProgressCacheKey()" in source
    assert "resettable_review_count" in source
    assert "current_event_ids" in source
    assert "undoable_event_ids" in source
    assert "Saved history is unchanged" in source
    assert "does not alter scientific corrections or the audit history" in source
    assert "change this decision" in source
    assert 'application.reviewer_progress(parts[2], user["id"])' in server
    assert 'application.undo_mutation(' in server
    assert 'application.reset_reviewer_state(' in server


def test_admin_can_download_all_reviewer_feedback():
    html = (APP / "index.html").read_text(encoding="utf-8")
    source = (APP / "app.js").read_text(encoding="utf-8")
    server = (APP.parent / "server.py").read_text(encoding="utf-8")

    assert 'id="download-all-feedback"' in html
    assert 'payload.user.role !== "admin"' in source
    assert "/api/reviewer-feedback-export" in source
    assert "application.reviewer_feedback_archive()" in server
    assert "self.current_user(require_admin=True)" in server


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
    assert "Work down the short Record review checklist" in html
    assert "only when a scalar value is wrong" in html
    assert "Download Excel for this device" in source
    assert "downloadReviewWorkbook" in source
    assert "uploadReviewWorkbook" in source
    assert "/api/review-workbook/" in source
    assert "application.review_workbook(" in server
    assert "application.import_review_workbook(" in server
    assert "one validated revision and archived" in source
    assert "original Excel file could not be archived" in source
    assert "comments_only_from_older_workbook" in source
    assert "its value edits were not applied" in source
    assert "one validated revision" in source
