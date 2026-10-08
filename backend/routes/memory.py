"""
================================================================
记忆管理路由 — memory.py
================================================================
提供跨会话记忆的查看和管理 API：

- GET  /api/memory      列出所有记忆
- DELETE /api/memory/{id} 删除一条记忆
"""

import logging
from fastapi import APIRouter, HTTPException
from backend.services import memory_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["记忆"])


@router.get("/memory")
async def list_memories():
    """列出所有已存储的记忆事实"""
    return await memory_service.get_all()


@router.delete("/memory/{fact_id}")
async def delete_memory(fact_id: str):
    """删除指定的记忆事实"""
    deleted = await memory_service.delete_one(fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"deleted": True, "fact_id": fact_id}
