"""
================================================================
工具注册中心 — tool_registry.py
================================================================
管理所有 Function Calling 工具的注册和分发。
添加新工具只需在此注册，无需改动其他代码。
"""

import json
import logging
from backend.tools import python_executor
from backend.tools import plot_chart

logger = logging.getLogger(__name__)

# 工具注册表：name → {schema, handler}
REGISTRY = {
    "run_python": {
        "schema": python_executor.SCHEMA,
        "handler": python_executor.execute,
    },
    # 图表走浏览器端渲染（不执行代码）：模型只输出图表配置，前端 ECharts 画图
    "plot_chart": {
        "schema": plot_chart.SCHEMA,
        "handler": plot_chart.execute,
    },
}


def get_tool_schemas() -> list[dict]:
    """返回所有工具的 OpenAI Function Calling Schema 列表"""
    return [t["schema"] for t in REGISTRY.values()]


def dispatch(tool_name: str, tool_args: dict) -> str:
    """执行工具并返回格式化结果字符串"""
    logger.info(f"🔧 工具调用: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

    tool = REGISTRY.get(tool_name)
    if not tool:
        return f"错误：未知工具 '{tool_name}'。可用：{', '.join(REGISTRY.keys())}"

    try:
        handler = tool["handler"]
        # 根据参数特征分发
        import inspect
        sig = inspect.signature(handler)
        if "action" in sig.parameters:
            action = tool_args.pop("action", None)
            result = handler(action=action, **(tool_args if tool_args else {}))
        else:
            result = handler(**tool_args)

        result_str = json.dumps(result, ensure_ascii=False, indent=2)
        logger.info(f"✅ 工具结果: {result_str[:200]}")
        return result_str
    except Exception as e:
        logger.error(f"❌ 工具执行失败: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
