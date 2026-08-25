# ruff: noqa: E402

import base64
import json

import pytest

jwt = pytest.importorskip("jwt", reason="JWT support is isolated to the Vercel workbench")

from review_workbench.auth import (
    AuthenticationError,
    ClerkAuthenticator,
    InternalAuthenticator,
    InternalOrClerkAuthenticator,
    clerk_key_allowed,
    hash_password,
)
from review_workbench.server import ReviewApplication


def publishable_key(domain="example.clerk.accounts.dev"):
    encoded = base64.urlsafe_b64encode(f"{domain}$".encode()).decode().rstrip("=")
    return f"pk_test_{encoded}"


def test_development_clerk_keys_are_not_used_in_production():
    assert clerk_key_allowed("pk_test_example", "preview") is True
    assert clerk_key_allowed("pk_test_example", "production") is False
    assert clerk_key_allowed("pk_live_example", "production") is True


def test_clerk_authenticator_maps_allowlisted_account_to_admin(monkeypatch):
    auth = ClerkAuthenticator(
        publishable_key=publishable_key(),
        secret_key="sk_test_example",
        admin_emails="admin@example.org",
        reviewer_emails="reviewer@example.org",
    )
    monkeypatch.setattr(
        auth.jwks,
        "get_signing_key_from_jwt",
        lambda token: type("Key", (), {"key": "public-key"})(),
    )
    monkeypatch.setattr(
        "review_workbench.auth.jwt.decode",
        lambda *args, **kwargs: {"sub": "user_123"},
    )
    monkeypatch.setattr(
        auth,
        "_user",
        lambda user_id: {
            "id": user_id,
            "first_name": "Ada",
            "last_name": "Reviewer",
            "primary_email_address_id": "email_1",
            "email_addresses": [
                {"id": "email_1", "email_address": "admin@example.org"}
            ],
        },
    )

    user = auth.authenticate({"Authorization": "Bearer session-token"})

    assert user == {
        "id": "user_123",
        "email": "admin@example.org",
        "name": "Ada Reviewer",
        "role": "admin",
    }


def test_clerk_authenticator_rejects_uninvited_account(monkeypatch):
    auth = ClerkAuthenticator(
        publishable_key=publishable_key(),
        secret_key="sk_test_example",
        admin_emails="admin@example.org",
        reviewer_emails="",
    )
    monkeypatch.setattr(
        auth.jwks,
        "get_signing_key_from_jwt",
        lambda token: type("Key", (), {"key": "public-key"})(),
    )
    monkeypatch.setattr(
        "review_workbench.auth.jwt.decode",
        lambda *args, **kwargs: {"sub": "user_456"},
    )
    monkeypatch.setattr(
        auth,
        "_user",
        lambda user_id: {
            "id": user_id,
            "primary_email_address_id": "email_2",
            "email_addresses": [
                {"id": "email_2", "email_address": "stranger@example.org"}
            ],
        },
    )

    with pytest.raises(AuthenticationError, match="not an invited reviewer"):
        auth.authenticate({"Authorization": "Bearer session-token"})


def test_authenticated_users_are_upserted_by_provider_id(tmp_path):
    user = {
        "id": "user_123",
        "name": "Ada Reviewer",
        "email": "ada@example.org",
        "role": "reviewer",
    }

    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    app.ensure_authenticated_user(user)
    app.ensure_authenticated_user({**user, "name": "Ada R."})

    stored = [entry for entry in app.users() if entry["id"] == "user_123"]
    assert stored == [{**user, "name": "Ada R."}]


def test_internal_authenticator_logs_in_and_verifies_session():
    accounts = json.dumps(
        {
            "admin@example.org": {
                "name": "Ada Admin",
                "role": "admin",
                "password_hash": hash_password("correct horse", iterations=1_000),
            }
        }
    )
    auth = InternalAuthenticator(accounts, "s" * 32)

    token, user = auth.login("ADMIN@example.org", "correct horse")

    assert user["email"] == "admin@example.org"
    assert user["role"] == "admin"
    assert auth.authenticate({"Authorization": f"Bearer {token}"}) == user


def test_internal_authenticator_rejects_bad_password():
    accounts = json.dumps(
        {
            "reviewer@example.org": {
                "password_hash": hash_password("right", iterations=1_000)
            }
        }
    )
    auth = InternalAuthenticator(accounts, "s" * 32)

    with pytest.raises(AuthenticationError, match="incorrect"):
        auth.login("reviewer@example.org", "wrong")


def test_internal_authenticator_merges_additive_accounts(monkeypatch):
    monkeypatch.setenv(
        "REVIEW_INTERNAL_ACCOUNT_ADDITIONS",
        json.dumps(
            {
                "new@example.org": {
                    "name": "New Reviewer",
                    "role": "reviewer",
                    "password_hash": hash_password("new password", iterations=1_000),
                }
            }
        ),
    )
    base = json.dumps(
        {
            "existing@example.org": {
                "password_hash": hash_password("existing", iterations=1_000)
            }
        }
    )

    auth = InternalAuthenticator(base, "s" * 32)

    assert auth.login("existing@example.org", "existing")[1]["email"] == "existing@example.org"
    assert auth.login("new@example.org", "new password")[1]["name"] == "New Reviewer"


def test_internal_authenticator_applies_password_overrides_last(monkeypatch):
    monkeypatch.setenv(
        "REVIEW_INTERNAL_ACCOUNT_OVERRIDES",
        json.dumps(
            {
                "reviewer@example.org": {
                    "name": "Current Reviewer",
                    "password_hash": hash_password("rotated", iterations=1_000),
                }
            }
        ),
    )
    auth = InternalAuthenticator(
        json.dumps(
            {
                "reviewer@example.org": {
                    "name": "Old Reviewer",
                    "password_hash": hash_password("old", iterations=1_000),
                }
            }
        ),
        "s" * 32,
    )

    assert auth.login("reviewer@example.org", "rotated")[1]["name"] == "Current Reviewer"
    with pytest.raises(AuthenticationError, match="incorrect"):
        auth.login("reviewer@example.org", "old")


def test_internal_or_clerk_authenticator_preserves_password_login_and_routes_tokens(
    monkeypatch,
):
    internal = InternalAuthenticator(
        json.dumps(
            {
                "reviewer@example.org": {
                    "password_hash": hash_password("existing", iterations=1_000)
                }
            }
        ),
        "s" * 32,
    )
    clerk = ClerkAuthenticator(
        publishable_key=publishable_key(),
        secret_key="sk_test_example",
        admin_emails="",
        reviewer_emails="reviewer@example.org",
    )
    auth = InternalOrClerkAuthenticator(internal, clerk)

    internal_token, internal_user = auth.login("reviewer@example.org", "existing")
    assert auth.authenticate({"Authorization": f"Bearer {internal_token}"}) == internal_user

    clerk_user = {
        "id": "user_123",
        "email": "reviewer@example.org",
        "name": "Email Reviewer",
        "role": "reviewer",
    }
    monkeypatch.setattr(clerk, "authenticate", lambda headers: clerk_user)
    clerk_token = jwt.encode({"sub": "user_123"}, "k" * 48, algorithm="HS384")
    migrated_user = {**clerk_user, "id": internal_user["id"]}
    assert auth.authenticate({"Authorization": f"Bearer {clerk_token}"}) == migrated_user
    assert auth.authenticate({"Cookie": "__session=clerk-cookie"}) == migrated_user

    config = auth.public_config()
    assert config["enabled"] is True
    assert config["mode"] == "internal_or_clerk"
    assert config["frontend_api"] == "https://example.clerk.accounts.dev"
