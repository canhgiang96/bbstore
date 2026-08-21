from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from .auth import AuthError, verify_access_token


def get_current_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Thiếu token đăng nhập.")
    token = auth_header[len("Bearer "):]
    try:
        payload = verify_access_token(token)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return {
        "id": payload["sub"],
        "email": payload.get("email"),
        "role": payload.get("role"),
        "display_name": payload.get("display_name"),
    }


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Admin mới được thực hiện thao tác này.")
    return user
