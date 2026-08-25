"""Authentication providers for the deployed review workbench."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from functools import lru_cache
from http.cookies import SimpleCookie
from urllib.parse import quote
from urllib.request import Request, urlopen

import jwt


class AuthenticationError(PermissionError):
    """An authentication or authorization failure safe to return to clients."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def hash_password(password: str, *, iterations: int = 600_000) -> str:
    """Return a portable PBKDF2 hash suitable for a Vercel environment value."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, iterations
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(iterations),
            base64.urlsafe_b64encode(salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        )
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(raw_salt + "=" * (-len(raw_salt) % 4))
        expected = base64.urlsafe_b64decode(
            raw_digest + "=" * (-len(raw_digest) % 4)
        )
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, int(raw_iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class InternalAuthenticator:
    """Authenticate a small deployment without adding an external identity service.

    Account configuration stays server-side, passwords are stored only as salted
    PBKDF2 hashes, and signed sessions are accepted only while the account remains in
    the current allowlist.
    """

    def __init__(
        self,
        accounts_json: str | None = None,
        session_secret: str | None = None,
    ):
        raw_accounts = (
            accounts_json
            if accounts_json is not None
            else os.environ.get("REVIEW_INTERNAL_ACCOUNTS", "")
        )
        self.session_secret = session_secret or os.environ.get(
            "REVIEW_SESSION_SECRET", ""
        )
        try:
            loaded = json.loads(raw_accounts) if raw_accounts else {}
        except json.JSONDecodeError as error:
            raise ValueError("REVIEW_INTERNAL_ACCOUNTS is not valid JSON") from error
        if not isinstance(loaded, dict):
            raise ValueError("REVIEW_INTERNAL_ACCOUNTS must be a JSON object")
        raw_additions = os.environ.get("REVIEW_INTERNAL_ACCOUNT_ADDITIONS", "")
        try:
            additions = json.loads(raw_additions) if raw_additions else {}
        except json.JSONDecodeError as error:
            raise ValueError(
                "REVIEW_INTERNAL_ACCOUNT_ADDITIONS is not valid JSON"
            ) from error
        if not isinstance(additions, dict):
            raise ValueError("REVIEW_INTERNAL_ACCOUNT_ADDITIONS must be a JSON object")
        loaded = {**loaded, **additions}
        self.accounts = {
            str(email).strip().lower(): account
            for email, account in loaded.items()
            if isinstance(account, dict)
        }

    @property
    def configured(self) -> bool:
        return bool(self.accounts and len(self.session_secret) >= 32)

    def public_config(self) -> dict[str, object]:
        return {"enabled": self.configured, "mode": "internal"}

    @staticmethod
    def _token(headers) -> str:
        authorization = headers.get("Authorization", "")
        return (
            authorization.removeprefix("Bearer ").strip()
            if authorization.startswith("Bearer ")
            else ""
        )

    @staticmethod
    def _user(email: str, account: dict) -> dict[str, str]:
        identifier = hashlib.sha256(email.encode()).hexdigest()[:20]
        return {
            "id": f"internal_{identifier}",
            "email": email,
            "name": str(account.get("name") or email),
            "role": "admin" if account.get("role") == "admin" else "reviewer",
        }

    def login(self, email: str, password: str) -> tuple[str, dict[str, str]]:
        """Issue a bounded session only after hash verification and role lookup."""

        if not self.configured:
            raise AuthenticationError("Authentication is not configured", 503)
        normalized = email.strip().lower()
        account = self.accounts.get(normalized)
        encoded = str(account.get("password_hash", "")) if account else ""
        if not account or not _verify_password(password, encoded):
            raise AuthenticationError("Email or password is incorrect")
        user = self._user(normalized, account)
        now = int(time.time())
        token = jwt.encode(
            {"sub": user["id"], "email": normalized, "iat": now, "exp": now + 604800},
            self.session_secret,
            algorithm="HS256",
        )
        return token, user

    def authenticate(self, headers) -> dict[str, str]:
        """Recheck session identity against the live account list on every request."""

        if not self.configured:
            raise AuthenticationError("Authentication is not configured", 503)
        token = self._token(headers)
        if not token:
            raise AuthenticationError("Sign in is required")
        try:
            claims = jwt.decode(token, self.session_secret, algorithms=["HS256"])
        except Exception as error:
            raise AuthenticationError("The session is invalid or expired") from error
        email = str(claims.get("email", "")).lower()
        account = self.accounts.get(email)
        user = self._user(email, account or {})
        if not account or claims.get("sub") != user["id"]:
            raise AuthenticationError("This account is no longer enabled", 403)
        return user


def _frontend_api(publishable_key: str) -> str:
    try:
        encoded = publishable_key.split("_", 2)[-1]
        encoded += "=" * (-len(encoded) % 4)
        domain = base64.urlsafe_b64decode(encoded).decode().removesuffix("$")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Invalid Clerk publishable key") from error
    return f"https://{domain}"


def _emails(value: str) -> set[str]:
    return {email.strip().lower() for email in value.split(",") if email.strip()}


class ClerkAuthenticator:
    """Layer repository-controlled authorization on top of Clerk authentication.

    A valid Clerk identity is insufficient by itself: the primary email must also be
    present in the reviewer or administrator allowlist configured for this deployment.
    """

    def __init__(
        self,
        publishable_key: str | None = None,
        secret_key: str | None = None,
        admin_emails: str | None = None,
        reviewer_emails: str | None = None,
    ):
        self.publishable_key = publishable_key or os.environ.get(
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", ""
        )
        self.secret_key = secret_key or os.environ.get("CLERK_SECRET_KEY", "")
        self.admin_emails = _emails(
            admin_emails
            if admin_emails is not None
            else os.environ.get("REVIEW_ADMIN_EMAILS", "")
        )
        self.reviewer_emails = _emails(
            reviewer_emails
            if reviewer_emails is not None
            else os.environ.get("REVIEW_USER_EMAILS", "")
        )
        self.allowed_emails = self.admin_emails | self.reviewer_emails
        self.frontend_api = (
            _frontend_api(self.publishable_key) if self.publishable_key else ""
        )
        self.jwks = (
            jwt.PyJWKClient(f"{self.frontend_api}/.well-known/jwks.json")
            if self.frontend_api
            else None
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.publishable_key
            and self.secret_key
            and self.jwks
            and self.allowed_emails
        )

    def public_config(self) -> dict[str, object]:
        return {
            "enabled": self.configured,
            "mode": "clerk",
            "publishable_key": self.publishable_key if self.configured else "",
            "frontend_api": self.frontend_api if self.configured else "",
        }

    @staticmethod
    def _token(headers) -> str:
        authorization = headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            return authorization.removeprefix("Bearer ").strip()
        cookie = SimpleCookie()
        cookie.load(headers.get("Cookie", ""))
        return cookie.get("__session").value if cookie.get("__session") else ""

    @lru_cache(maxsize=64)
    def _user(self, user_id: str) -> dict:
        request = Request(
            f"https://api.clerk.com/v1/users/{quote(user_id)}",
            headers={"Authorization": f"Bearer {self.secret_key}"},
        )
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def authenticate(self, headers) -> dict[str, str]:
        """Verify Clerk's signature and issuer before applying the local allowlist."""

        if not self.configured:
            raise AuthenticationError("Authentication is not configured", 503)
        token = self._token(headers)
        if not token:
            raise AuthenticationError("Sign in is required")
        try:
            signing_key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self.frontend_api,
                options={"verify_aud": False},
            )
            user = self._user(str(claims["sub"]))
        except Exception as error:
            raise AuthenticationError("The session is invalid or expired") from error

        primary_id = user.get("primary_email_address_id")
        email = next(
            (
                item.get("email_address", "").lower()
                for item in user.get("email_addresses", [])
                if item.get("id") == primary_id
            ),
            "",
        )
        if email not in self.allowed_emails:
            raise AuthenticationError("This account is not an invited reviewer", 403)
        name = " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        )
        return {
            "id": str(user["id"]),
            "email": email,
            "name": name or email,
            "role": "admin" if email in self.admin_emails else "reviewer",
        }


class InternalOrClerkAuthenticator:
    """Keep fixed accounts usable while reviewers move to recoverable sign-in.

    Internal sessions use HS256 and Clerk sessions use its asymmetric signing keys,
    so the JWT header can route a request without attempting both providers. This is
    intentionally a migration boundary: Clerk supplies email recovery, while existing
    project passwords continue to work until every reviewer has moved across.
    """

    def __init__(
        self,
        internal: InternalAuthenticator | None = None,
        clerk: ClerkAuthenticator | None = None,
    ):
        self.internal = internal or InternalAuthenticator()
        self.clerk = clerk or ClerkAuthenticator()

    @property
    def configured(self) -> bool:
        return self.internal.configured and self.clerk.configured

    def public_config(self) -> dict[str, object]:
        config = self.clerk.public_config()
        return {**config, "enabled": self.configured, "mode": "internal_or_clerk"}

    def login(self, email: str, password: str) -> tuple[str, dict[str, str]]:
        """Preserve the fixed-account login endpoint during the migration."""

        return self.internal.login(email, password)

    def authenticate(self, headers) -> dict[str, str]:
        """Route bearer sessions by their signed algorithm; Clerk also accepts cookies."""

        token = self.internal._token(headers)
        if not token:
            return self._preserve_internal_identity(self.clerk.authenticate(headers))
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except Exception as error:
            raise AuthenticationError("The session is invalid or expired") from error
        if algorithm == "HS256":
            return self.internal.authenticate(headers)
        return self._preserve_internal_identity(self.clerk.authenticate(headers))

    def _preserve_internal_identity(self, user: dict[str, str]) -> dict[str, str]:
        """Keep one review history when the same email changes sign-in provider."""

        email = user.get("email", "").lower()
        account = self.internal.accounts.get(email)
        if not account:
            return user
        return {**user, "id": self.internal._user(email, account)["id"]}
