"""
================================================================
记忆服务 — memory_service.py
================================================================
跨会话持久记忆的核心逻辑：

1. extract_and_save() — 对话结束后，用 LLM 提取用户事实，存入 DB
2. search() — 新消息到达时，检索相关记忆
3. get_all() / delete() — 记忆管理
"""

import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from backend.db import get_db

logger = logging.getLogger(__name__)
TZ_CST = timezone(timedelta(hours=8))


def _now():
    return datetime.now(TZ_CST).isoformat()


# ================================================================
# 搜索相关记忆
# ================================================================


async def search_relevant(query: str, limit: int = 5) -> list[dict]:
    """
    搜索与当前消息相关的记忆

    策略：
    1. 从 query 中提取关键词（简单的中文二元组分词 + 英文空格分词）
    2. 在 memory_facts 中查找包含任一关键词的记录
    3. 按「访问次数 + 创建时间」加权排序
    4. 返回 top-N 结果

    参数：
        query: 用户当前消息
        limit: 最多返回多少条记忆

    返回：
        相关记忆列表
    """
    keywords = _extract_keywords(query)
    if not keywords:
        return []

    async with get_db() as db:
        # 同时搜索 keywords 和 fact_text 字段
        conditions = " OR ".join(
            ["(keywords LIKE ? OR fact_text LIKE ?)" for _ in keywords]
        )
        params = []
        for kw in keywords:
            params.extend([f"%{kw}%", f"%{kw}%"])
        sql = f"""
            SELECT * FROM memory_facts
            WHERE {conditions}
            ORDER BY access_count DESC, created_at DESC
            LIMIT ?
        """
        params.append(limit)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()

    results = [dict(row) for row in rows]

    # 更新访问计数
    if results:
        async with get_db() as db:
            for r in results:
                await db.execute(
                    "UPDATE memory_facts SET access_count = access_count + 1 WHERE id = ?",
                    (r["id"],),
                )
            await db.commit()

    return results


# ================================================================
# 提取并保存新记忆
# ================================================================


async def extract_and_save(session_id: str, user_message: str, ai_reply: str):
    """
    从对话中提取用户事实并保存

    使用简单的规则提取（避免额外 LLM 调用）：
    - 匹配 "我叫/我是/我喜欢/我..." 等自我描述模式
    - 拆分句子，筛选包含用户相关关键词的句子

    参数：
        session_id: 来源会话 ID
        user_message: 用户消息
        ai_reply: AI 回复（用于上下文判断）
    """
    facts = _simple_extract(user_message)

    if not facts:
        return

    async with get_db() as db:
        for fact_text in facts:
            # 检查是否已存在相同记忆（去重）
            cursor = await db.execute(
                "SELECT id FROM memory_facts WHERE fact_text = ?",
                (fact_text,),
            )
            existing = await cursor.fetchone()
            if existing:
                continue

            fact_id = uuid.uuid4().hex
            keywords = json.dumps(_extract_keywords(fact_text), ensure_ascii=False)
            now = _now()

            await db.execute(
                """INSERT INTO memory_facts
                   (id, fact_text, keywords, source_session_id, created_at, access_count)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (fact_id, fact_text, keywords, session_id, now),
            )
            logger.info(f"🧠 新记忆: {fact_text}")

        await db.commit()


# ================================================================
# 记忆管理
# ================================================================


async def get_all(limit: int = 50) -> list[dict]:
    """列出所有记忆"""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM memory_facts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def delete_one(fact_id: str) -> bool:
    """删除一条记忆"""
    async with get_db() as db:
        cursor = await db.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))
        await db.commit()
        return cursor.rowcount > 0


# ================================================================
# 关键词提取（简单本地实现，不依赖外部库）
# ================================================================


def _extract_keywords(text: str) -> list[str]:
    """
    从文本中提取关键词

    中文：使用二元组分词（bigram），覆盖大部分双字词
    英文/数字：按空格和标点分词
    """
    keywords = []
    text = text.strip()

    # 提取中文连续片段（CJK 字符范围）
    chinese_chars = ""
    other_words = []

    import re
    for ch in text:
        if "一" <= ch <= "鿿":  # CJK 统一汉字
            chinese_chars += ch
        else:
            # 非中文部分累积
            if ch.isalpha() or ch.isdigit():
                other_words.append(ch)

    # 中文二元组
    for i in range(len(chinese_chars) - 1):
        keywords.append(chinese_chars[i:i + 2])
    # 也加入中文单字（提高召回率）
    for ch in chinese_chars:
        keywords.append(ch)

    # 英文/数字按空格分词
    english_part = re.findall(r"[a-zA-Z0-9]+", text)
    keywords.extend([w.lower() for w in english_part if len(w) > 1])

    # 去重
    return list(set(keywords))


# ================================================================
# 规则提取 — 从用户消息中找出自我描述
# ================================================================


def _simple_extract(text: str) -> list[str]:
    """
    从用户消息中提取事实（规则匹配）

    匹配模式：
    - "我叫X" → "用户叫X"
    - "我是X" → "用户是X"
    - "我喜欢X" → "用户喜欢X"
    - "我的X是Y" → "用户的X是Y"
    - "我在X" → "用户在X"
    - "我想X" → "用户想X"
    """
    import re
    facts = []

    patterns = [
        (r"我叫(.+?)(?:[，。！？\s]|$)", lambda m: f"用户叫{m.group(1)}"),
        (r"我是(.+?)(?:[，。！？\s]|$)", lambda m: f"用户是{m.group(1)}"),
        (r"我喜欢(.+?)(?:[，。！？\s]|$)", lambda m: f"用户喜欢{m.group(1)}"),
        (r"我想(.+?)(?:[，。！？\s]|$)", lambda m: f"用户想{m.group(1)}"),
        (r"我在(.+?)(?:[，。！？\s]|$)", lambda m: f"用户在{m.group(1)}"),
        (r"我的(.{1,10})是(.+?)(?:[，。！？\s]|$)", lambda m: f"用户的{m.group(1)}是{m.group(2)}"),
        (r"我不喜欢(.+?)(?:[，。！？\s]|$)", lambda m: f"用户不喜欢{m.group(1)}"),
    ]

    for pattern, formatter in patterns:
        match = re.search(pattern, text)
        if match:
            fact = formatter(match)
            if len(fact) >= 4 and len(fact) < 100:  # "用户叫X" 至少4字
                # 过滤掉问句（不小心把用户提问当事实提取了）
                if "什么" in fact or "吗" in fact or "？" in fact or "？" in fact:
                    continue
                facts.append(fact)

    return facts
