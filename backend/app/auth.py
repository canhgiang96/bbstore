"""Login/refresh.

The browser never holds a Supabase session. login() verifies the password
against Supabase Auth once, looks up the matching `profiles` row, and
issues our own short-lived JWT (access) + long-lived JWT (refresh), both
signed with APP_JWT_SECRET. Every subsequent /api/* call carries only our
token — see app/deps.py for verification.
"""
from __future__ import annotations

import time

import httpx
import jwt

from .config import get_settings
from .db import pg_select_one


class AuthError(Exception):
    pass


async def _verify_supabase_password(email: str, password: str) -> str:
    """Returns the Supabase auth user id, or raises AuthError."""
    s = get_settings()
    url = f"{s.supabase_url}/auth/v1/token?grant_type=password"
    headers = {"apikey": s.supabase_service_role_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, headers=headers, json={"email": email, "password": password})
    if r.status_code != 200:
        raise AuthError("Sai email hoặc mật khẩu.")
    data = r.json()
    return data["user"]["id"]


def _issue_token(user_id: str, extra: dict, ttl_seconds: int, token_type: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {"sub": user_id, "type": token_type, "iat": now, "exp": now + ttl_seconds, **extra}
    return jwt.encode(payload, s.app_jwt_secret, algorithm="HS256")


async def _profile_or_raise(user_id: str) -> dict:
    profile = await pg_select_one("profiles", {"id": f"eq.{user_id}"})
    if not profile:
        raise AuthError("Tài khoản chưa được cấp quyền truy cập.")
    return profile


async def login(email: str, password: str) -> dict:
    user_id = await _verify_supabase_password(email, password)
    profile = await _profile_or_raise(user_id)
    return _tokens_for(user_id, profile)


async def refresh(refresh_token: str) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(refresh_token, s.app_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise AuthError("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.")
    if payload.get("type") != "refresh":
        raise AuthError("Token không hợp lệ.")

    user_id = payload["sub"]
    profile = await _profile_or_raise(user_id)
    # Re-issues a fresh access token but keeps the same refresh token — role
    # changes made in Supabase take effect on the next refresh, not instantly.
    s = get_settings()
    access_token = _issue_token(
        user_id,
        {"email": profile["email"], "role": profile["role"], "display_name": profile["display_name"]},
        s.app_jwt_access_ttl_seconds,
        "access",
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": s.app_jwt_access_ttl_seconds,
        "role": profile["role"],
        "display_name": profile["display_name"],
    }


def _tokens_for(user_id: str, profile: dict) -> dict:
    s = get_settings()
    claims = {"email": profile["email"], "role": profile["role"], "display_name": profile["display_name"]}
    access_token = _issue_token(user_id, claims, s.app_jwt_access_ttl_seconds, "access")
    refresh_token = _issue_token(user_id, {}, s.app_jwt_refresh_ttl_seconds, "refresh")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": s.app_jwt_access_ttl_seconds,
        "role": profile["role"],
        "display_name": profile["display_name"],
    }


def verify_access_token(token: str) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.app_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise AuthError("Token không hợp lệ hoặc đã hết hạn.")
    if payload.get("type") != "access":
        raise AuthError("Token không đúng loại.")
    return payload
