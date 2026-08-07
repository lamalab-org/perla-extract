import base64
import json

import pytest

pytest.importorskip("jwt", reason="JWT support is isolated to the Vercel workbench")

from review_workbench.auth import (
    AuthenticationError,
    ClerkAuthenticator,
    InternalAuthenticator,
    hash_password,
)
from review_workbench.review_collaboration import (
    load_users,
    upsert_authenticated_user,
)


def publishable_key(domain="example.clerk.accounts.dev"):
    encoded = base64.urlsafe_b64encode(f"{domain}$".encode()).decode().rstrip("=")
    return f"pk_test_{encoded}"


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

    upsert_authenticated_user(tmp_path, user)
    upsert_authenticated_user(tmp_path, {**user, "name": "Ada R."})

    stored = [entry for entry in load_users(tmp_path) if entry["id"] == "user_123"]
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
