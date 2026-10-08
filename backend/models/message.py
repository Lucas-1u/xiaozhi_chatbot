"""
================================================================
消息模型 — message.py
================================================================
定义消息相关的 Pydantic 数据模型。
"""

from pydantic import BaseModel


class MessageOut(BaseModel):
    """
    消息输出模型 — API 返回的单条消息

    包含消息角色、内容和可选的工具调用信息。
    """
    id: str
    session_id: str
    role: str                # user / assistant / tool
    content: str | None = None
    tool_calls_json: str | None = None  # 工具调用 JSON 字符串
    created_at: str
