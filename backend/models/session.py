"""
================================================================
会话模型 — session.py
================================================================
定义会话相关的 Pydantic 数据模型，用于 API 的请求/响应序列化。
"""

from pydantic import BaseModel
from datetime import datetime


class SessionOut(BaseModel):
    """
    会话输出模型 — API 返回的会话信息

    包含会话基本信息和消息数量，供前端侧栏展示。
    """
    id: str
    title: str = "新对话"
    created_at: str        # ISO 8601 格式时间字符串
    updated_at: str
    message_count: int = 0  # 该会话的消息总数（侧栏预览用）
