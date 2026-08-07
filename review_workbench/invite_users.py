#!/usr/bin/env python3
"""Invite a small, explicit reviewer list through Clerk's Backend API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def dotenv_value(path: Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def invite(secret_key: str, email: str, role: str) -> str:
    body = json.dumps(
        {
            "email_address": email,
            "notify": True,
            "public_metadata": {"review_role": role},
        }
    ).encode()
    request = Request(
        "https://api.clerk.com/v1/invitations",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "perla-review-workbench/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return str(json.load(response).get("status", "created"))
    except HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"errors": [], "response": raw.decode(errors="replace")[:200]}
        codes = {item.get("code") for item in payload.get("errors", [])}
        if codes & {"duplicate_record", "form_identifier_exists"}:
            return "already invited or registered"
        raise RuntimeError(
            f"Clerk rejected {email} with HTTP {error.code}: {payload}"
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("emails", nargs="+")
    parser.add_argument("--admin", action="append", default=[])
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    secret = os.environ.get("CLERK_SECRET_KEY", "")
    if not secret and args.env_file:
        secret = dotenv_value(args.env_file, "CLERK_SECRET_KEY")
    if not secret:
        parser.error("CLERK_SECRET_KEY is not configured")
    admins = {email.lower() for email in args.admin}
    for email in args.emails:
        normalized = email.strip().lower()
        role = "admin" if normalized in admins else "reviewer"
        print(f"{normalized}: {invite(secret, normalized, role)}")


if __name__ == "__main__":
    main()
