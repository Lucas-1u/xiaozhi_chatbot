"""
================================================================
聊天路由 — chat.py
================================================================
处理聊天消息的 API 端点。

Phase 2 版本：
- POST /api/chat/{session_id} — 发送消息，加载历史、保存回复、自动标题
- 每次请求会先加载会话历史消息，加上 system prompt 和当前用户消息
- LLM 回复完成后自动保存到数据库
- 首次对话后自动为会话生成标题（取用户第一句话的前 30 字）
"""

import asyncio
import json
import logging
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from backend.services.stream_handler import generate_sse_events
from backend.services import session_manager, memory_service
from backend.prompts.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["聊天"])


class ChatRequest(BaseModel):
    """
    聊天请求体
    """
    content: str = Field(
        ...,
        min_length=1,
        description="用户输入的消息内容",
    )
    enable_search: bool = Field(
        False,
        description="是否启用联网搜索（由前端开关控制，默认关闭）",
    )
    enable_thinking: bool = Field(
        False,
        description="是否启用深度思考（由前端开关控制，默认关闭）",
    )


@router.post("/chat/{session_id}")
async def chat_with_session(session_id: str, request_body: ChatRequest):
    """
    向指定会话发送消息并获取流式回复

    流程：
    1. 验证会话是否存在
    2. 加载该会话的历史消息
    3. 加上 system prompt 和当前用户消息，构建完整 messages 列表
    4. 调用 LLM 流式 API
    5. 保存用户消息和 AI 回复到数据库
    6. 首次对话后自动生成标题

    参数：
        session_id: 会话 ID（路径参数）

    返回：
        text/event-stream 流式响应
    """

    user_content = request_body.content.strip()
    logger.info(
        f"会话 {session_id[:8]}... 收到消息: {user_content[:50]}..."
        f" | 联网搜索: {'开' if request_body.enable_search else '关'}"
        f" | 深度思考: {'开' if request_body.enable_thinking else '关'}"
    )

    # ================================================================
    # 1. 验证会话存在
    # ================================================================
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # ================================================================
    # 2. 搜索相关记忆 & 构建 messages 列表
    # ================================================================
    history_messages = session.get("messages", [])

    # 搜索与当前消息相关的跨会话记忆
    found_memories = await memory_service.search_relevant(user_content)
    memory_texts = [m["fact_text"] for m in found_memories]
    if memory_texts:
        logger.info(f"🧠 召回 {len(memory_texts)} 条记忆: {memory_texts}")

    # 构建 messages：动态 system prompt + 历史 + 当前消息
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(memory_texts if memory_texts else None),
        },
    ]

    # 加入历史消息
    # 注意：必须保留工具调用的痕迹（tool_calls / tool_call_id），
    # 否则模型在历史里只看到「用户提问 → 我回复已生成」，会误以为不用调用工具，
    # 从第三轮开始就只回文字"已为您生成图表"而不真正调用 plot_chart。
    for msg in history_messages:
        role = msg.get("role")
        if role == "system":
            continue

        entry = {"role": role, "content": msg.get("content") or ""}
        raw_tc = msg.get("tool_calls_json")
        parsed_tc = None
        if raw_tc:
            try:
                parsed_tc = json.loads(raw_tc)
            except Exception:
                parsed_tc = None

        if role == "assistant" and parsed_tc:
            # 带工具调用的助手消息：content 允许为空，tool_calls 必须原样回放
            entry["content"] = msg.get("content") or None
            entry["tool_calls"] = parsed_tc
        elif role == "tool" and isinstance(parsed_tc, dict):
            # 工具结果消息：必须带上配对的 tool_call_id，否则 API 会报错
            tc_id = parsed_tc.get("tool_call_id")
            if not tc_id:
                continue
            entry["tool_call_id"] = tc_id

        messages.append(entry)

    # 当前用户消息
    messages.append({
        "role": "user",
        "content": user_content,
    })

    # ================================================================
    # 3. 保存用户消息到数据库
    # ================================================================
    await session_manager.add_message(session_id, "user", user_content)

    # ================================================================
    # 4. 自动标题：如果是第一条消息，用消息内容生成标题
    # ================================================================
    if len(history_messages) == 0:
        title = user_content[:30] + ("..." if len(user_content) > 30 else "")
        await session_manager.update_session_title(session_id, title)

    # ================================================================
    # 5. 返回 SSE 流式响应（带后处理：完成后保存 AI 回复）
    # ================================================================
    async def sse_wrapper():
        """
        SSE 流式响应包装器
        - 逐个产出 SSE 事件给前端
        - 累积 AI 回复的完整文本
        - 流结束后保存 AI 回复到数据库
        """
        full_content = ""

        # 工具调用痕迹（用于写入历史，防止模型下一轮回话时"只说不做"）
        tool_calls_record = []    # [{id, type, function:{name, arguments}}]
        tool_results_record = []  # [{id, summary}]
        charts_record = []        # 本轮生成的图表配置（ECharts option），随回复一起入库

        # generate_sse_events 是异步生成器，用 async for 遍历
        async for event_str in generate_sse_events(
            messages=messages,
            enable_search=request_body.enable_search,
            enable_thinking=request_body.enable_thinking,
        ):
            # 解析 delta 事件，提取文本内容用于累积
            if "event: delta" in event_str:
                try:
                    data_line = event_str.split("data: ", 1)[1]
                    data_line = data_line.split("\n")[0]
                    data = json.loads(data_line)
                    full_content += data.get("content", "")
                except Exception:
                    pass

            # 收集工具调用痕迹
            elif "event: tool_call" in event_str:
                try:
                    data = json.loads(event_str.split("data: ", 1)[1].split("\n")[0])
                    tool_calls_record.append({
                        "id": data.get("tool_call_id") or "",
                        "type": "function",
                        "function": {
                            "name": data.get("tool_name", ""),
                            "arguments": json.dumps(data.get("tool_args") or {}, ensure_ascii=False),
                        },
                    })
                except Exception as e:
                    logger.warning(f"记录工具调用失败: {e}")

            elif "event: tool_result" in event_str:
                try:
                    data = json.loads(event_str.split("data: ", 1)[1].split("\n")[0])
                    raw_res = data.get("tool_result") or ""
                    # 只存一行摘要，避免把整份图表配置塞进历史（会撑爆上下文）
                    summary = raw_res
                    try:
                        obj = json.loads(raw_res)
                        summary = obj.get("output") or obj.get("error") or raw_res
                    except Exception:
                        pass
                    tool_results_record.append({
                        "id": data.get("tool_call_id") or "",
                        "summary": str(summary)[:300],
                    })
                    # 图表配置一并收集，稍后随 AI 回复存库（刷新页面后还能重绘）
                    for spec in (data.get("charts") or []):
                        charts_record.append(spec)
                except Exception as e:
                    logger.warning(f"记录工具结果失败: {e}")

            # 将 SSE 事件字符串转为字节流发送
            yield event_str.encode("utf-8")

    async def persist_result(
        full_content: str,
        tool_calls_record: list,
        tool_results_record: list,
        charts_record: list,
        cancelled: bool,
    ):
        """
        流结束后落库。抽成独立协程是为了：客户端中途断开（「停止生成」）时
        可以用 create_task 把它丢到后台跑完，不会被取消掉。

        顺序不能乱：先存工具调用痕迹，再存最终回复，这样历史长这样：
            user → assistant(tool_calls) → tool(结果) → assistant(最终回复)
        模型下一轮看到"确实调用过工具"，就不会跳过工具直接回文字。
        """
        try:
            if tool_calls_record:
                await session_manager.add_message(
                    session_id, "assistant", None,
                    tool_calls_json=json.dumps(tool_calls_record, ensure_ascii=False),
                )
                for tr in tool_results_record:
                    await session_manager.add_message(
                        session_id, "tool", tr["summary"],
                        tool_calls_json=json.dumps({"tool_call_id": tr["id"]}, ensure_ascii=False),
                    )
                logger.info(
                    f"会话 {session_id[:8]}... 已保存 {len(tool_calls_record)} 条工具调用痕迹"
                )

            if full_content:
                await session_manager.add_message(
                    session_id, "assistant", full_content,
                    charts_json=(json.dumps(charts_record, ensure_ascii=False) if charts_record else None),
                )
                logger.info(
                    f"会话 {session_id[:8]}... AI 回复已保存 ({len(full_content)} 字"
                    + ("，用户已停止" if cancelled else "")
                    + ")"
                    + (f"，含 {len(charts_record)} 张图表配置" if charts_record else "")
                )

                # 用户主动停止的半截回答不提取记忆（内容不完整，容易存进错的东西）
                if not cancelled:
                    await memory_service.extract_and_save(
                        session_id, user_content, full_content
                    )
        except Exception as e:
            logger.error(f"保存回复失败: {e}", exc_info=True)

    async def sse_wrapper():
        """
        SSE 流式响应包装器
        - 逐个产出 SSE 事件给前端
        - 累积 AI 回复的完整文本
        - 流结束后保存 AI 回复到数据库
        - 客户端中途断开（停止生成）时，已生成的内容照样存下来
        """
        full_content = ""

        # 工具调用痕迹（用于写入历史，防止模型下一轮回话时"只说不做"）
        tool_calls_record = []    # [{id, type, function:{name, arguments}}]
        tool_results_record = []  # [{id, summary}]
        charts_record = []        # 本轮生成的图表配置（ECharts option），随回复一起入库
        cancelled = False

        try:
            # generate_sse_events 是异步生成器，用 async for 遍历
            async for event_str in generate_sse_events(
                messages=messages,
                enable_search=request_body.enable_search,
                enable_thinking=request_body.enable_thinking,
            ):
                # 解析 delta 事件，提取文本内容用于累积
                if "event: delta" in event_str:
                    try:
                        data_line = event_str.split("data: ", 1)[1]
                        data_line = data_line.split("\n")[0]
                        data = json.loads(data_line)
                        full_content += data.get("content", "")
                    except Exception:
                        pass

                # 收集工具调用痕迹
                elif "event: tool_call" in event_str:
                    try:
                        data = json.loads(event_str.split("data: ", 1)[1].split("\n")[0])
                        tool_calls_record.append({
                            "id": data.get("tool_call_id") or "",
                            "type": "function",
                            "function": {
                                "name": data.get("tool_name", ""),
                                "arguments": json.dumps(data.get("tool_args") or {}, ensure_ascii=False),
                            },
                        })
                    except Exception as e:
                        logger.warning(f"记录工具调用失败: {e}")

                elif "event: tool_result" in event_str:
                    try:
                        data = json.loads(event_str.split("data: ", 1)[1].split("\n")[0])
                        raw_res = data.get("tool_result") or ""
                        # 只存一行摘要，避免把整份图表配置塞进历史（会撑爆上下文）
                        summary = raw_res
                        try:
                            obj = json.loads(raw_res)
                            summary = obj.get("output") or obj.get("error") or raw_res
                        except Exception:
                            pass
                        tool_results_record.append({
                            "id": data.get("tool_call_id") or "",
                            "summary": str(summary)[:300],
                        })
                        # 图表配置一并收集，稍后随 AI 回复存库（刷新页面后还能重绘）
                        for spec in (data.get("charts") or []):
                            charts_record.append(spec)
                    except Exception as e:
                        logger.warning(f"记录工具结果失败: {e}")

                # 将 SSE 事件字符串转为字节流发送
                yield event_str.encode("utf-8")

        except asyncio.CancelledError:
            # 用户点了「停止生成」，或关掉了页面
            cancelled = True
            logger.info(
                f"会话 {session_id[:8]}... 客户端断开，已生成 {len(full_content)} 字，"
                f"转后台保存"
            )
            # 用 create_task 而不是 await：此时当前协程正在被取消，
            # 直接 await 会被立刻打断，已生成的内容就丢了
            asyncio.create_task(persist_result(
                full_content, tool_calls_record, tool_results_record, charts_record, True
            ))
            raise

        # 正常跑完
        await persist_result(
            full_content, tool_calls_record, tool_results_record, charts_record, cancelled
        )

    return StreamingResponse(
        sse_wrapper(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
