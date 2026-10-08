"""
================================================================
流式处理引擎 — stream_handler.py
================================================================
聊天核心编排器：调用 LLM → 处理工具调用 → 生成 SSE 事件

Phase 3 完整流程：
1. 调用 LLM 流式 API（带 tools 定义）
2. 普通文本 → delta 事件流式输出
3. LLM 请求工具 → 执行工具 → 结果回流 → 继续生成
4. 最多循环 5 次（防止死循环）
"""

import json
import logging
import time

from backend.config import settings
from backend.services.llm_client import LLMClient, LLMClientError
from backend.services.tool_registry import get_tool_schemas, dispatch

logger = logging.getLogger(__name__)

llm_client = LLMClient()

# 工具调用最大循环次数（每轮 = 一次模型请求 + 一次工具执行）
# 一次复杂的数据分析往往是「读文件 → 看结构 → 聚合 → 画图」多轮接力，
# 10 轮偏紧，容易在分析到一半时被截断；30 轮足够放开手脚，
# 死循环风险由下面的「重复调用熔断」兜底。
MAX_TOOL_ITERATIONS = settings.max_tool_iterations

# 熔断：连续多次发起**完全相同**的工具调用（同名 + 同参数）就判定为死循环
MAX_SAME_CALL_REPEAT = settings.max_same_call_repeat


async def generate_sse_events(
    messages: list[dict],
    enable_tools: bool = True,
    enable_search: bool = False,
    enable_thinking: bool = False,
):
    """
    生成 SSE 事件流（异步生成器）

    参数：
        messages: 对话消息列表（system + history + user）
        enable_tools: 是否启用工具调用（默认 True）
        enable_search: 是否启用联网搜索（默认 False）。
            由前端开关逐请求传入，不影响全局默认行为。
        enable_thinking: 是否启用深度思考（默认 False），同样由前端开关控制。
    """
    tools = get_tool_schemas() if enable_tools else None
    iteration = 0
    same_call_streak = 0        # 连续相同调用的次数
    last_call_signature = ""    # 上一轮工具调用的签名（函数名 + 参数）

    while True:
        iteration += 1

        # ================================================================
        # 安全阀 1：迭代次数上限
        # ================================================================
        if iteration > MAX_TOOL_ITERATIONS:
            logger.warning(
                f"工具调用达到上限 {MAX_TOOL_ITERATIONS} 轮，主动停止"
                f"（会话 messages 长度 {len(messages)}）"
            )
            yield _sse(
                "delta",
                {
                    "content": (
                        f"\n\n[已达到最大工具调用次数（{MAX_TOOL_ITERATIONS} 轮），"
                        f"已停止执行。可以让我先给个阶段性结论，再继续下一步。]"
                    )
                },
            )
            yield _sse("done", {"finish_reason": "max_iterations"})
            return

        # ================================================================
        # 安全阀 2：重复调用熔断
        # 模型如果陷入「用一模一样的参数反复调同一个工具」的死循环，
        # 光靠次数上限会白白烧掉几十轮 token，所以这里提前刹车。
        # ================================================================
        if same_call_streak >= MAX_SAME_CALL_REPEAT:
            logger.warning(
                f"检测到连续 {same_call_streak} 次相同的工具调用，熔断停止"
                f"（最后一次：{last_call_signature[:80]}）"
            )
            yield _sse(
                "delta",
                {
                    "content": (
                        "\n\n[检测到工具在重复调用同一操作，已停止以避免空转。"
                        "建议换个思路：换列名、换聚合方式，或让我先说明卡在哪一步。]"
                    )
                },
            )
            yield _sse("done", {"finish_reason": "repeated_tool_call"})
            return

        # ================================================================
        # 本轮状态
        # ================================================================
        full_content = ""       # 本轮 LLM 的完整文本输出
        tool_calls_buffer: list[dict] = []  # 累积工具调用参数

        try:
            # 每轮都带上工具定义：
            # 模型经常需要「多工具接力」——例如先用 run_python 读取上传的 CSV，
            # 再把数据交给 plot_chart 画成可交互图表。
            # 如果只在第一轮给工具，第二轮模型就"看不见" plot_chart，只能反复硬用
            # run_python 去画图（甚至撞满循环上限）。
            # 死循环风险由 MAX_TOOL_ITERATIONS 兜底。
            tools_this_round = tools
            stream = llm_client.chat_completion_stream(
                messages=messages,
                tools=tools_this_round,
                enable_search=enable_search,
                enable_thinking=enable_thinking,
            )

            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason

                # ---- 情况 0：思考过程增量（仅开启深度思考时出现） ----
                # 必须在 delta.content 之前处理：DashScope 在思考阶段只会
                # 返回 reasoning_content，正式作答阶段才返回 content。
                reasoning = _extract_reasoning(delta)
                if reasoning:
                    yield _sse("reasoning", {"content": reasoning})

                # ---- 情况 1：文本增量 ----
                if delta and delta.content:
                    full_content += delta.content
                    yield _sse("delta", {"content": delta.content})

                # ---- 情况 2：工具调用增量 ----
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        # 确保 buffer 容量足够
                        while len(tool_calls_buffer) <= tc.index:
                            tool_calls_buffer.append({"id": "", "function": {"name": "", "arguments": ""}})

                        buf = tool_calls_buffer[tc.index]
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                buf["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                buf["function"]["arguments"] += tc.function.arguments

                # ---- 情况 3：流结束 ----
                if finish_reason:
                    if finish_reason == "tool_calls" and tool_calls_buffer:
                        # ============================================
                        # LLM 请求调用工具 → 执行 → 结果回流
                        # ============================================

                        # 1. 先把 LLM 的消息（含 tool_calls）加入 messages
                        messages.append({
                            "role": "assistant",
                            "content": full_content or None,
                            "tool_calls": tool_calls_buffer,
                        })

                        # 2. 逐个执行工具并收集结果
                        #    先算本轮调用签名（函数名 + 参数），用于重复调用熔断
                        signature = "|".join(
                            f"{tc['function']['name']}:{tc['function']['arguments'][:600]}"
                            for tc in tool_calls_buffer
                        )
                        if signature and signature == last_call_signature:
                            same_call_streak += 1
                        else:
                            same_call_streak = 1
                            last_call_signature = signature

                        for tc in tool_calls_buffer:
                            name = tc["function"]["name"]
                            try:
                                args = json.loads(tc["function"]["arguments"])
                            except json.JSONDecodeError:
                                args = {}

                            # 通知前端（tool_call_id 供后端把「调用痕迹」写进历史）
                            started_at = time.perf_counter()
                            yield _sse("tool_call", {
                                "tool_call_id": tc["id"],
                                "tool_name": name,
                                "tool_args": args,
                            })

                            # 执行工具
                            result_str = dispatch(name, args)
                            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)

                            # 提取生成的图表：图片（run_python）和图表配置（plot_chart）
                            try:
                                _result_obj = json.loads(result_str)
                                plots = _result_obj.get("plots", [])
                                charts = _result_obj.get("charts", [])
                            except Exception:
                                plots = []
                                charts = []

                            # 通知前端（charts 由前端 ECharts 渲染成可交互图表）
                            yield _sse("tool_result", {
                                "tool_call_id": tc["id"],
                                "tool_name": name,
                                "tool_result": result_str,
                                "plots": plots,
                                "charts": charts,
                                "elapsed_ms": elapsed_ms,
                            })

                            # 工具结果加入 messages（每个工具一条 tool 消息）
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result_str,
                            })

                        # 跳出内层循环，重新调用 LLM
                        break

                    else:
                        # 正常结束（stop）或达到长度限制（length）
                        yield _sse("done", {"finish_reason": finish_reason})
                        return

        except LLMClientError as e:
            logger.error(f"LLM 调用错误: {e}")
            yield _sse("error", {"error": str(e)})
            return
        except Exception as e:
            logger.error(f"流式处理错误: {e}", exc_info=True)
            yield _sse("error", {"error": f"服务内部错误: {str(e)}"})
            return


def _sse(event_type: str, data: dict) -> str:
    """格式化 SSE 消息"""
    data_json = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data_json}\n\n"


def _extract_reasoning(delta) -> str | None:
    """
    从 chunk.delta 中提取「思考过程」文本。

    背景：百炼在开启 enable_thinking 后，会在流式响应的 delta 里额外返回
    reasoning_content 字段（与正式的 content 分开推送）。
    由于它不是 OpenAI 标准字段，openai SDK 未必把它声明为模型属性，
    可能塞进 model_extra，所以这里做两级兜底读取。

    参数：
        delta: chunk.choices[0].delta

    返回：
        思考过程文本；没有则返回 None
    """
    if delta is None:
        return None

    # 一级：SDK 直接暴露了该属性
    value = getattr(delta, "reasoning_content", None)

    # 二级：字段落在 model_extra 里（pydantic v2 对未知字段的处理）
    if value is None:
        extra = getattr(delta, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get("reasoning_content")

    return value or None
