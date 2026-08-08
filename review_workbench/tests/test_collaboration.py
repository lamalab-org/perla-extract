from review_workbench.review_collaboration import (
    add_comment,
    add_issue,
    add_user,
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
