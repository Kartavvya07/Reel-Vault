"""Authentication utilities for Reel Vault personal vault."""

from datetime import datetime, timezone
import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.spike.config import settings

COOKIE_NAME = "vault_token"


def get_expected_token() -> Optional[str]:
    """Returns the configured shared password/token from environment."""
    return settings.vault_auth_token or settings.spike_api_key


def is_authenticated(request: Request) -> bool:
    """
    Checks whether the incoming request contains the valid shared vault token/password.
    Checks:
    1. Cookie 'vault_token'
    2. Header 'X-Vault-Token' or 'X-Spike-Key'
    3. Header 'Authorization: Bearer <token>'
    4. Query parameter '?token=<token>'
    """
    expected = get_expected_token()

    # In development/test mode only, if no secret is configured, allow access
    if not expected:
        if not settings.is_production:
            return True
        return False

    # 1. Cookie
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token and secrets.compare_digest(cookie_token, expected):
        return True

    # 2. Custom header
    header_token = request.headers.get("X-Vault-Token") or request.headers.get("X-Spike-Key")
    if header_token and secrets.compare_digest(header_token, expected):
        return True

    # 3. Authorization Bearer header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header.split(" ", 1)[1].strip()
        if bearer_token and secrets.compare_digest(bearer_token, expected):
            return True

    # 4. Query param
    query_token = request.query_params.get("token")
    if query_token and secrets.compare_digest(query_token, expected):
        return True

    return False


def set_auth_cookie(response: Response, token: str) -> None:
    """Sets a secure HTTP-only cookie with the authentication token."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=60 * 60 * 24 * 30,  # 30 days
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


def clear_auth_cookie(response: Response) -> None:
    """Clears the authentication cookie on logout."""
    response.delete_cookie(key=COOKIE_NAME)

