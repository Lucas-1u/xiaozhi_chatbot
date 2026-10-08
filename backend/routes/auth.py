"""
================================================================
鉴权路由 — auth.py
================================================================
- GET  /api/auth/status  前端启动时问：这个站要不要口令？
- POST /api/auth         提交口令，换取访问 token
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services import auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["鉴权"])


class AuthRequest(BaseModel):
    password: str = Field(..., description="访问口令")


@router.get("/auth/status")
async def auth_status():
    """前端启动时调用：判断是否需要显示口令输入界面"""
    return {
        "required": auth.auth_enabled(),
        # token 已存在且有效时直接返回，前端可以跳过输入
        "ok": True,
    }


@router.post("/auth")
async def auth_login(body: AuthRequest):
    """
    用口令换 token。

    不启用口令时直接返回空 token（前端照常工作）。
    """
    if not auth.auth_enabled():
        return {"ok": True, "required": False, "token": ""}

    if not auth.verify_password(body.password):
        logger.warning("访问口令校验失败")
        raise HTTPException(status_code=401, detail="口令不正确")

    logger.info("✅ 访问口令校验通过")
    return {"ok": True, "required": True, "token": auth.derive_token()}
