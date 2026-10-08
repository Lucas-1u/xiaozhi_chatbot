"""
================================================================
会话管理服务 — session_manager.py
================================================================
会话和消息的 CRUD 操作。支持 user_token 隔离会话。
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from backend.db import get_db

logger = logging.getLogger(__name__)
TZ_CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(TZ_CST).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ================================================================
# 会话操作
# ================================================================

async def create_session(title: str = "新对话", user_token: str = "") -> dict:
    """创建一个新会话（绑定用户令牌）"""
    session_id = _new_id()
    now = _now()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, user_token) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, now, now, user_token),
        )
        await db.commit()

    logger.info(f"创建会话: {session_id} ({title})")
    return {"id": session_id, "title": title, "created_at": now, "updated_at": now}


async def list_sessions(user_token: str = "") -> list[dict]:
    """列出指定用户的会话（按更新时间降序）"""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
            WHERE s.user_token = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC
        """, (user_token,))
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_session(session_id: str) -> dict | None:
    """获取单个会话详情（含消息列表）"""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        )
        session_row = await cursor.fetchone()
        if not session_row:
            return None
        session = dict(session_row)
        cursor = await db.execute(
            "SELECT id, session_id, role, content, tool_calls_json, charts_json, created_at "
            "FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        session["messages"] = [dict(row) for row in await cursor.fetchall()]
        return session


async def delete_session(session_id: str) -> bool:
    """删除会话（外键级联删除消息）"""
    async with get_db() as db:
        cursor = await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()
        return cursor.rowcount > 0


async def update_session_title(session_id: str, title: str) -> bool:
    """更新会话标题"""
    now = _now()
    async with get_db() as db:
        await db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )
        await db.commit()
    return True


# ================================================================
# 消息操作
# ================================================================

async def add_message(
    session_id: str, role: str, content: str | None = None,
    tool_calls_json: str | None = None,
    charts_json: str | None = None,
) -> dict:
    """向会话中添加消息"""
    message_id = _new_id()
    now = _now()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (id, session_id, role, content, tool_calls_json, charts_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, session_id, role, content, tool_calls_json, charts_json, now),
        )
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id),
        )
        await db.commit()
    return {"id": message_id, "session_id": session_id, "role": role,
            "content": content, "tool_calls_json": tool_calls_json,
            "charts_json": charts_json, "created_at": now}


async def get_session_messages(session_id: str) -> list[dict]:
    """获取会话的所有消息（按时间升序）"""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, session_id, role, content, tool_calls_json, charts_json, created_at "
            "FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]
