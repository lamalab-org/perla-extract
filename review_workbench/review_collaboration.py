"""Durable named-reviewer and discussion records for the review workbench."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _collaboration_dir(ground_truth_dir: Path) -> Path:
    return Path(ground_truth_dir) / "collaboration"


def _users_path(ground_truth_dir: Path) -> Path:
    return _collaboration_dir(ground_truth_dir) / "users.json"


def load_users(ground_truth_dir: Path) -> list[dict[str, str]]:
    path = _users_path(ground_truth_dir)
    if not path.exists():
        return [{"id": "reviewer", "name": "Reviewer"}]
    with path.open(encoding="utf-8") as stream:
        return json.load(stream).get("users", [])


def add_user(ground_truth_dir: Path, name: str) -> dict[str, str]:
    name = re.sub(r"\s+", " ", name).strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("Reviewer name must contain 1-80 characters")
    users = load_users(ground_truth_dir)
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "reviewer"
    user_id = base
    if any(user["id"] == user_id for user in users):
        user_id = f"{base}-{uuid.uuid4().hex[:6]}"
    user = {"id": user_id, "name": name}
    users.append(user)
    path = _users_path(ground_truth_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "users": users}, indent=2) + "\n",
        encoding="utf-8",
    )
    return user


def upsert_authenticated_user(
    ground_truth_dir: Path, user: dict[str, str]
) -> dict[str, str]:
    """Persist a trusted identity supplied by the authentication provider."""
    user_id = str(user.get("id", "")).strip()
    name = re.sub(r"\s+", " ", str(user.get("name", ""))).strip()
    email = str(user.get("email", "")).strip().lower()
    role = str(user.get("role", "reviewer"))
    if not user_id or not 1 <= len(name) <= 120 or "@" not in email:
        raise ValueError("Invalid authenticated reviewer")
    record = {"id": user_id, "name": name, "email": email, "role": role}
    users = load_users(ground_truth_dir)
    users = [existing for existing in users if existing.get("id") != user_id]
    users.append(record)
    path = _users_path(ground_truth_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "users": users}, indent=2) + "\n",
        encoding="utf-8",
    )
    return record


def _comments_path(ground_truth_dir: Path, split: str, paper_id: str) -> Path:
    return _collaboration_dir(ground_truth_dir) / "comments" / split / f"{paper_id}.json"


def load_comments(
    ground_truth_dir: Path, split: str, paper_id: str
) -> list[dict[str, Any]]:
    path = _comments_path(ground_truth_dir, split, paper_id)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return json.load(stream).get("comments", [])


def add_comment(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    author_id: str,
    body: str,
    field_path: str | None = None,
) -> dict[str, Any]:
    if author_id not in {user["id"] for user in load_users(ground_truth_dir)}:
        raise ValueError("Unknown reviewer")
    body = body.strip()
    if not 1 <= len(body) <= 4000:
        raise ValueError("Comment must contain 1-4000 characters")
    comments = load_comments(ground_truth_dir, split, paper_id)
    comment = {
        "id": uuid.uuid4().hex,
        "author_id": author_id,
        "body": body,
        "field_path": field_path,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    comments.append(comment)
    path = _comments_path(ground_truth_dir, split, paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": 1, "paper_id": paper_id, "comments": comments},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return comment


ISSUE_TYPES = {
    "missing_cell",
    "missing_value",
    "missing_layer",
    "missing_composition",
    "mixed_device",
    "schema_limitation",
    "wrong_value",
    "other",
}


def _figure_audit_path(ground_truth_dir: Path, split: str, paper_id: str) -> Path:
    return (
        _collaboration_dir(ground_truth_dir)
        / "figure_audits"
        / split
        / f"{paper_id}.json"
    )


def load_figure_audits(
    ground_truth_dir: Path, split: str, paper_id: str
) -> dict[str, dict[str, Any]]:
    path = _figure_audit_path(ground_truth_dir, split, paper_id)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        return json.load(stream).get("audits", {})


def save_figure_audit(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    reviewer_id: str,
    payload: object,
) -> dict[str, Any]:
    if reviewer_id not in {user["id"] for user in load_users(ground_truth_dir)}:
        raise ValueError("Unknown reviewer")
    if not isinstance(payload, dict):
        raise ValueError("Figure audit must be an object")
    counts = {}
    for key in (
        "total_figures",
        "schema_relevant_figures",
        "figure_only_schema_figures",
    ):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("Figure counts must be non-negative integers")
        counts[key] = value
    if counts["schema_relevant_figures"] > counts["total_figures"]:
        raise ValueError("Schema-relevant figures cannot exceed total figures")
    if counts["figure_only_schema_figures"] > counts["schema_relevant_figures"]:
        raise ValueError("Figure-only count cannot exceed schema-relevant figures")
    notes = str(payload.get("notes", "")).strip()
    if len(notes) > 4000:
        raise ValueError("Figure audit notes must contain at most 4000 characters")
    audit = {
        **counts,
        "notes": notes,
        "reviewer_id": reviewer_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    audits = load_figure_audits(ground_truth_dir, split, paper_id)
    audits[reviewer_id] = audit
    path = _figure_audit_path(ground_truth_dir, split, paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": 1, "paper_id": paper_id, "audits": audits},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return audit


def _issues_path(ground_truth_dir: Path, split: str, paper_id: str) -> Path:
    return _collaboration_dir(ground_truth_dir) / "issues" / split / f"{paper_id}.json"


def load_issues(
    ground_truth_dir: Path, split: str, paper_id: str
) -> list[dict[str, Any]]:
    path = _issues_path(ground_truth_dir, split, paper_id)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return json.load(stream).get("issues", [])


def save_issues(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    issues: list[dict[str, Any]],
) -> None:
    path = _issues_path(ground_truth_dir, split, paper_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": 1, "paper_id": paper_id, "issues": issues},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def add_issue(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    reporter_id: str,
    issue_type: str,
    description: str,
    *,
    cell_index: int | None = None,
    field_path: str | None = None,
    suggested_value: str = "",
    source_page: int | None = None,
    source_text: str = "",
) -> dict[str, Any]:
    if reporter_id not in {user["id"] for user in load_users(ground_truth_dir)}:
        raise ValueError("Unknown reviewer")
    if issue_type not in ISSUE_TYPES:
        raise ValueError("Unknown issue type")
    description = description.strip()
    if not 1 <= len(description) <= 4000:
        raise ValueError("Issue description must contain 1-4000 characters")
    if cell_index is not None and (not isinstance(cell_index, int) or cell_index < 0):
        raise ValueError("Cell index must be a non-negative integer")
    if source_page is not None and (not isinstance(source_page, int) or source_page < 1):
        raise ValueError("Source page must be a positive integer")
    issues = load_issues(ground_truth_dir, split, paper_id)
    issue = {
        "id": uuid.uuid4().hex,
        "type": issue_type,
        "status": "open",
        "reporter_id": reporter_id,
        "description": description,
        "cell_index": cell_index,
        "field_path": field_path,
        "suggested_value": str(suggested_value),
        "source_page": source_page,
        "source_text": str(source_text),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": None,
        "resolution": "",
        "resolved_at": None,
    }
    issues.append(issue)
    save_issues(ground_truth_dir, split, paper_id, issues)
    return issue


def resolve_issue(
    ground_truth_dir: Path,
    split: str,
    paper_id: str,
    issue_id: str,
    reviewer_id: str,
    resolution: str,
) -> dict[str, Any]:
    if reviewer_id not in {user["id"] for user in load_users(ground_truth_dir)}:
        raise ValueError("Unknown reviewer")
    issues = load_issues(ground_truth_dir, split, paper_id)
    issue = next((item for item in issues if item["id"] == issue_id), None)
    if issue is None:
        raise ValueError("Issue not found")
    issue["status"] = "resolved"
    issue["resolved_by"] = reviewer_id
    issue["resolution"] = resolution.strip()
    issue["resolved_at"] = datetime.now(timezone.utc).isoformat()
    save_issues(ground_truth_dir, split, paper_id, issues)
    return issue
