"""
================================================================
数据库模块 — db.py
================================================================
负责 SQLite 数据库的初始化和连接管理。

使用 aiosqlite 实现异步数据库操作，与 FastAPI 的异步特性兼容。
SQLite 数据库文件存储在项目根目录的 data/ 文件夹下。
启用 WAL 模式提高并发性能，开启外键约束保证数据完整性。

使用方式：
    from backend.db import get_db
    async with get_db() as db:
        await db.execute("SELECT ...")
"""

import os
import aiosqlite
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# ========== 数据库文件路径 ==========
# 数据目录：项目根目录下的 data/ 文件夹
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "chat.db")


@asynccontextmanager
async def get_db():
    """
    获取数据库连接的异步上下文管理器

    使用 async with 语法自动管理连接的打开和关闭：
        async with get_db() as db:
            await db.execute("SELECT * FROM sessions")
    """
    db = await aiosqlite.connect(DB_PATH)
    # 启用 WAL 模式：允许并发读写，提升性能
    await db.execute("PRAGMA journal_mode=WAL")
    # 启用外键约束：确保数据完整性（如级联删除）
    await db.execute("PRAGMA foreign_keys=ON")
    # row_factory 让查询结果以字典形式返回（而非元组）
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db():
    """
    初始化数据库：创建数据目录和所有表

    在应用启动时调用一次。使用 IF NOT EXISTS 确保幂等性（重复执行不会出错）。
    建表语句基于规划中的 SQL Schema。
    """
    # 确保数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)

    async with get_db() as db:
        # ================================================================
        # 1. 会话表（sessions）
        # ================================================================
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,          -- UUID4 主键
                title       TEXT DEFAULT '新对话',      -- 会话标题，默认为"新对话"
                created_at  TEXT NOT NULL,              -- 创建时间（ISO 8601 格式）
                updated_at  TEXT NOT NULL               -- 最后更新时间
            )
        """)

        # ================================================================
        # 2. 消息表（messages）
        # ================================================================
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,           -- UUID4 主键
                session_id      TEXT NOT NULL,              -- 所属会话 ID
                role            TEXT NOT NULL,              -- 角色：user / assistant / tool
                content         TEXT,                       -- 消息文本内容
                tool_calls_json TEXT,                       -- 工具调用 JSON（Phase 3 使用）
                charts_json     TEXT,                       -- 图表配置 JSON（ECharts option，刷新后重绘用）
                created_at      TEXT NOT NULL,              -- 创建时间
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)

        # ================================================================
        # 3. 跨会话记忆表（memory_facts）— Phase 4 使用，提前建好
        # ================================================================
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memory_facts (
                id                TEXT PRIMARY KEY,
                fact_text         TEXT NOT NULL,           -- 记忆事实文本
                keywords          TEXT NOT NULL,           -- JSON 数组形式的关键词
                source_session_id TEXT,                    -- 来源会话 ID
                created_at        TEXT NOT NULL,
                access_count      INTEGER DEFAULT 0        -- 访问次数（用于排序）
            )
        """)

        # ================================================================
        # 4. 用户隐私令牌（ALTER TABLE，已有表也能加）
        # ================================================================
        try:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN user_token TEXT DEFAULT ''"
            )
        except Exception:
            pass  # 列已存在则忽略

        # ================================================================
        # 5. 图表配置列（ALTER TABLE，老数据库也能平滑升级）
        # ================================================================
        # 背景：以前图表只在「实时收到事件」那一刻渲染，刷新页面就没了。
        # 现在把 ECharts 配置存进消息行，刷新/换会话后依然能重绘出来。
        try:
            await db.execute(
                "ALTER TABLE messages ADD COLUMN charts_json TEXT"
            )
        except Exception:
            pass  # 列已存在则忽略

        # ================================================================
        # 5. 索引：加速按会话查询消息
        # ================================================================
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id)
        """)

        await db.commit()

    logger.info(f"数据库初始化完成: {DB_PATH}")
