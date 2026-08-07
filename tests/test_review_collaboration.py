from perla_extract.review_collaboration import (
    add_comment,
    add_issue,
    add_user,
    load_comments,
    load_issues,
    load_users,
    resolve_issue,
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
