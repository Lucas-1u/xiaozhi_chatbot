"""
================================================================
会话路由 — sessions.py
================================================================
"""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query
from backend.services import session_manager

router = APIRouter(prefix="/api", tags=["会话"])


class CreateSessionRequest(BaseModel):
    user_token: str = ""


@router.post("/sessions")
async def create_session_endpoint(body: CreateSessionRequest):
    """创建新会话（绑定用户令牌）"""
    return await session_manager.create_session(user_token=body.user_token)


@router.get("/sessions")
async def list_sessions_endpoint(user_token: str = Query("")):
    """列出指定用户的会话"""
    return await session_manager.list_sessions(user_token=user_token)


@router.get("/sessions/{session_id}")
async def get_session_endpoint(session_id: str):
    """获取会话详情"""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """删除会话"""
    deleted = await session_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"deleted": True, "session_id": session_id}
