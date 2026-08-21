from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth as auth_service
from ..deps import get_current_user
from ..models import LoginRequest, MeResponse, RefreshRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    try:
        return await auth_service.login(body.email, body.password)
    except auth_service.AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    try:
        return await auth_service.refresh(body.refresh_token)
    except auth_service.AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/me", response_model=MeResponse)
async def me(user: dict = Depends(get_current_user)):
    return MeResponse(**user)
