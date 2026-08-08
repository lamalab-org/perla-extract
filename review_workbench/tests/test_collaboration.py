from review_workbench.review_collaboration import (
    add_comment,
    add_issue,
    add_user,
    apply_proposed_patches,
    load_comments,
    load_issues,
    load_users,
    resolve_issue,
    load_figure_audits,
    save_figure_audit,
)


def test_users_and_field_comments_are_durable(tmp_path):
    user = add_user(tmp_path, "Ada Reviewer")
    comment = add_comment(
        tmp_path,
        "test",
        "paper",
        user["id"],
        "This looks like the stabilized value.",
        "/cells/0/pce/value",
    )

    assert user in load_users(tmp_path)
    assert load_comments(tmp_path, "test", "paper") == [comment]


def test_missing_item_issue_can_be_reported_and_resolved(tmp_path):
    user = add_user(tmp_path, "Ada Reviewer")
    issue = add_issue(
        tmp_path,
        "dev",
        "paper",
        user["id"],
        "missing_value",
        "The active area is stated in the caption.",
        cell_index=1,
        suggested_value="0.1 cm^2",
        source_page=4,
        source_text="active area of 0.1 cm2",
    )

    assert load_issues(tmp_path, "dev", "paper") == [issue]
    resolved = resolve_issue(
        tmp_path, "dev", "paper", issue["id"], user["id"], "Added to cell 2"
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "Added to cell 2"


def test_figure_audits_are_kept_separately_per_reviewer(tmp_path):
    first = add_user(tmp_path, "Ada")
    second = add_user(tmp_path, "Grace")
    save_figure_audit(
        tmp_path,
        "test",
        "paper",
        first["id"],
        {
            "total_figures": 6,
            "schema_relevant_figures": 3,
            "figure_only_schema_figures": 1,
            "unlinked_device_statistic_figures": 1,
            "notes": "Figure 3 contains a JV curve only.",
        },
    )
    save_figure_audit(
        tmp_path,
        "test",
        "paper",
        second["id"],
        {
            "total_figures": 6,
            "schema_relevant_figures": 4,
            "figure_only_schema_figures": 2,
            "notes": "Also counted the stability plot.",
        },
    )

    audits = load_figure_audits(tmp_path, "test", "paper")

    assert audits[first["id"]]["schema_relevant_figures"] == 3
    assert audits[first["id"]]["unlinked_device_statistic_figures"] == 1
    assert audits[second["id"]]["figure_only_schema_figures"] == 2


def test_schema_limitations_can_be_recorded_without_changing_ground_truth(tmp_path):
    user = add_user(tmp_path, "Ada")

    issue = add_issue(
        tmp_path,
        "test",
        "paper",
        user["id"],
        "schema_limitation",
        "The paper reports FF above 80%, not an exact value.",
        field_path="/cells/0/ff/value",
        source_page=5,
        value_relation="lower_bound",
        aggregation="champion",
        measurement_context="steady_state",
        uncertainty="Reported as above 80%.",
    )

    assert issue["type"] == "schema_limitation"
    assert issue["status"] == "open"
    assert issue["schema_proposal"] == {
        "value_relation": "lower_bound",
        "aggregation": "champion",
        "measurement_context": "steady_state",
        "uncertainty": "Reported as above 80%.",
    }


def test_issue_can_store_a_reviewable_json_patch(tmp_path):
    user = add_user(tmp_path, "Ada")
    patch = [
        {"op": "test", "path": "/cells/0/layers/1/additional_treatment", "value": "MFCl treatment"},
        {"op": "replace", "path": "/cells/0/layers/1/additional_treatment", "value": "BSP: MFCl in DMF"},
    ]

    issue = add_issue(
        tmp_path, "test", "paper", user["id"], "wrong_value",
        "The treatment is underspecified.", proposed_patch=patch,
    )

    assert issue["proposed_patch"] == patch


def test_open_issue_patches_build_non_destructive_revision():
    truth = {"cells": [{"pce": {"value": 20.0}}]}
    issues = [{
        "id": "fix-pce", "status": "open", "type": "wrong_value",
        "description": "The prose reports the champion value.",
        "source_page": 3, "source_text": "champion efficiency of 21.4%",
        "proposed_patch": [
            {"op": "test", "path": "/cells/0/pce/value", "value": 20.0},
            {"op": "replace", "path": "/cells/0/pce/value", "value": 21.4},
            {"op": "add", "path": "/cells/-", "value": {"pce": {"value": 19.2}}},
        ],
    }]

    preview = apply_proposed_patches(truth, issues)

    assert truth == {"cells": [{"pce": {"value": 20.0}}]}
    assert preview["proposed_ground_truth"]["cells"][0]["pce"]["value"] == 21.4
    assert len(preview["proposed_ground_truth"]["cells"]) == 2
    assert [change["path"] for change in preview["changes"]] == [
        "/cells/0/pce/value", "/cells/1"
    ]
    assert preview["conflicts"] == []
    assert preview["changes"][0]["change_id"] == "fix-pce:1"


def test_revision_can_apply_one_granular_change():
    truth = {"cells": [{"pce": {"value": 20.0}, "number_devices": None}]}
    issues = [{
        "id": "two-fixes", "status": "open", "type": "missing_value",
        "description": "Two independent corrections.",
        "proposed_patch": [
            {"op": "replace", "path": "/cells/0/pce/value", "value": 21.4},
            {"op": "replace", "path": "/cells/0/number_devices", "value": 20},
        ],
    }]

    preview = apply_proposed_patches(truth, issues, {"two-fixes:1"})

    assert preview["proposed_ground_truth"]["cells"][0]["pce"]["value"] == 20.0
    assert preview["proposed_ground_truth"]["cells"][0]["number_devices"] == 20
    assert [change["change_id"] for change in preview["changes"]] == ["two-fixes:1"]


def test_stale_patch_is_reported_and_not_partially_applied():
    truth = {"cells": [{"pce": {"value": 20.0}}]}
    issues = [{
        "id": "stale", "status": "open", "description": "Stale proposal",
        "proposed_patch": [
            {"op": "replace", "path": "/cells/0/pce/value", "value": 21.4},
            {"op": "test", "path": "/cells/0/pce/value", "value": 20.0},
        ],
    }]

    preview = apply_proposed_patches(truth, issues)

    assert preview["proposed_ground_truth"] == truth
    assert preview["changes"] == []
    assert preview["conflicts"][0]["issue_id"] == "stale"


def test_tandem_device_can_be_reported_as_out_of_scope(tmp_path):
    user = add_user(tmp_path, "Ada")

    issue = add_issue(
        tmp_path,
        "test",
        "paper",
        user["id"],
        "out_of_scope_tandem",
        "This record describes the complete tandem rather than a subcell.",
        cell_index=1,
        source_page=6,
    )

    assert issue["type"] == "out_of_scope_tandem"
