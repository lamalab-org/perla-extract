"""Clerk authentication for the deployed review workbench."""

from __future__ import annotations

import base64
import json
import os
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
    """Verify Clerk sessions and enforce the configured reviewer allowlist."""

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
