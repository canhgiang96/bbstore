"""Tests for the token issue/verify logic in app.auth — the parts that
don't need a live Supabase call (login()/refresh() do; those are exercised
manually after deployment per the plan's Phase 1 verification step).
"""
import time

import jwt
import pytest

from app.auth import AuthError, _issue_token, verify_access_token
from app.config import get_settings


def test_issue_and_verify_access_token_roundtrip():
    token = _issue_token("user-123", {"role": "admin", "email": "a@b.com", "display_name": "A"}, 3600, "access")
    payload = verify_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_rejected_by_verify_access_token():
    token = _issue_token("user-123", {}, 3600, "refresh")
    with pytest.raises(AuthError):
        verify_access_token(token)


def test_expired_token_rejected():
    s = get_settings()
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "user-123", "type": "access", "iat": now - 7200, "exp": now - 3600},
        s.app_jwt_secret, algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_access_token(expired)


def test_token_signed_with_wrong_secret_rejected():
    bad = jwt.encode(
        {"sub": "user-123", "type": "access", "exp": int(time.time()) + 3600},
        "wrong-secret", algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_access_token(bad)
