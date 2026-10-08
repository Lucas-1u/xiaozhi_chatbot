"""
================================================================
计算器工具 — calculator.py
================================================================
安全的算术计算工具，供 LLM 通过 Function Calling 调用。

安全策略：
- 受限 eval 环境，清除 __builtins__
- 仅白名单数学函数可用
- 输入长度限制 200 字符
"""

import math
import re

MAX_EXPRESSION_LENGTH = 200

# 白名单：只允许这些函数
SAFE_FUNCTIONS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "pow": pow, "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
    "pi": math.pi, "e": math.e,
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": (
            "执行安全的数学计算。支持四则运算、幂运算(^)、括号，"
            "以及数学函数：abs, round, min, max, pow, sqrt, ceil, floor。"
            "常量：pi, e。示例：'123 * 456'、'sqrt(144) + pow(2, 10)'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "要计算的数学表达式",
                }
            },
            "required": ["expression"],
        },
    },
}


def execute(expression: str) -> dict:
    """执行数学计算"""
    if not expression or not isinstance(expression, str):
        return {"success": False, "expression": str(expression), "error": "表达式不能为空"}

    expression = expression.strip()

    if len(expression) > MAX_EXPRESSION_LENGTH:
        return {"success": False, "expression": expression[:50] + "...",
                "error": f"表达式过长（最大 {MAX_EXPRESSION_LENGTH} 字符）"}

    # 安全校验：只允许数字、字母、运算符、空格、括号、小数点
    if not re.match(r'^[0-9a-zA-Z\s\+\-\*\/\(\)\.\,\^\%\_]+$', expression):
        return {"success": False, "expression": expression,
                "error": "表达式包含不允许的字符"}

    # ^ → **
    expression = expression.replace("^", "**")

    # 受限环境执行
    safe_ns = {"__builtins__": {}}
    safe_ns.update(SAFE_FUNCTIONS)

    try:
        result = eval(expression, safe_ns)
        if isinstance(result, float):
            if result == int(result) and abs(result) < 1e15:
                result = int(result)
            else:
                result = round(result, 10)
        return {"success": True, "expression": expression, "result": result}
    except SyntaxError as e:
        return {"success": False, "expression": expression, "error": f"语法错误: {e}"}
    except (ValueError, ArithmeticError, TypeError) as e:
        return {"success": False, "expression": expression, "error": f"计算错误: {e}"}
    except Exception as e:
        return {"success": False, "expression": expression, "error": f"未知错误: {e}"}
