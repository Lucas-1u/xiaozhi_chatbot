"""
================================================================
记忆模型 — memory.py
================================================================
跨会话持久记忆的数据模型。
"""

from pydantic import BaseModel


class MemoryFactOut(BaseModel):
    """记忆事实输出模型"""
    id: str
    fact_text: str          # "用户叫张三"
    keywords: str           # JSON: ["用户","张三"]
    source_session_id: str | None
    created_at: str
    access_count: int = 0
