"""
================================================================
访问口令 — auth.py
================================================================
部署到公网时的一道门：不设口令就完全放开（方便本地开发），
设了口令则 /api/* 业务接口都要求带有效凭证。

设计取舍：
- 口令本身**不下发**给前端，只下发一个由口令派生的 token；
  token 用 HMAC 派生 → 无状态、重启服务后依然有效，不用维护 session 表。
- 比对一律用 hmac.compare_digest（防时序攻击）。
- 静态资源（页面 / css / js）不拦，否则连"输入口令"的界面都加载不出来。
"""

import hashlib
import hmac
import logging

from backend.config import settings

logger = logging.getLogger(__name__)

# 放行清单：这几个接口不校验口令
PUBLIC_PATHS = {
    "/api/health",        # 部署脚本的健康检查
    "/api/auth",          # 用口令换 token
    "/api/auth/status",   # 前端启动时问"要不要输口令"
}


def auth_enabled() -> bool:
    """是否启用了访问口令（.env 里 ACCESS_PASSWORD 留空 = 不启用）"""
    return bool((settings.access_password or "").strip())


def derive_token() -> str:
    """
    由口令派生访问 token。

    无状态设计：同一个口令每次算出来都一样，所以服务重启后
    前端存在 localStorage 的 token 依然有效，用户不用反复输。
    """
    pw = (settings.access_password or "").strip()
    return hashlib.sha256(f"xiaoZhi-access::{pw}".encode("utf-8")).hexdigest()


def verify_password(password: str) -> bool:
    """校验用户输入的口令"""
    expected = (settings.access_password or "").strip()
    if not expected:
        return True
    return hmac.compare_digest((password or ""), expected)


def verify_token(token: str) -> bool:
    """校验请求携带的 token"""
    if not auth_enabled():
        return True
    return bool(token) and hmac.compare_digest(token, derive_token())


def is_public_path(path: str) -> bool:
    """该路径是否免鉴权"""
    if path in PUBLIC_PATHS:
        return True
    # 非 /api 开头的一律放行（前端页面、静态资源、/docs 等）
    return not path.startswith("/api/")
