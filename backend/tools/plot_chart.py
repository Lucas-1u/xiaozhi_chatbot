"""
================================================================
图表生成工具 — plot_chart.py
================================================================
让 LLM 把数据变成**浏览器端可交互图表**（不是图片）。

和 run_python 的分工：
- run_python   → 负责「算」：统计、筛选、聚合、复杂计算（也可画图导出图片）
- plot_chart   → 负责「画」：把数据组装成 ECharts 配置，交给前端渲染

为什么这么做（对比服务器 matplotlib 出图片）：
1. 快：不启子进程、不跑代码，直接返回配置 JSON（毫秒级）
2. 可交互：悬停看数值、点图例筛选、缩放、导出 PNG
3. 省服务器：画图算力在用户浏览器，服务器只出数据
4. 更安全：完全不执行模型写的代码
5. 中文正常：浏览器字体，不存在 matplotlib 字体缺失变方框的问题

本模块只做三件事：**校验参数 → 组装 ECharts option → 返回配置**
真正的画图发生在前端 charts.js 里（echarts.setOption）。
"""

import logging

logger = logging.getLogger(__name__)

# ========== 参数限制（防止模型塞入超大 payload） ==========
MAX_CATEGORIES = 60      # 类目轴最多 60 个点
MAX_SERIES = 8           # 最多 8 个系列
MAX_POINTS = 1000        # 单系列最多 1000 个数据点

# ========== 图表类型 ==========
SUPPORTED_TYPES = {
    "bar": "柱状图",
    "grouped_bar": "分组柱状图",
    "line": "折线图",
    "pie": "饼图",
    "scatter": "散点图",
    "heatmap": "热力图",
    "boxplot": "箱线图",
    "radar": "雷达图",
    "funnel": "漏斗图",
}

# 常见同义词 → 标准类型
TYPE_ALIASES = {
    "柱状图": "bar", "条形图": "bar", "bar_chart": "bar", "column": "bar",
    "分组柱状图": "grouped_bar", "分组柱状": "grouped_bar", "groupedbar": "grouped_bar", "grouped": "grouped_bar",
    "折线图": "line", "line_chart": "line", "趋势图": "line",
    "饼图": "pie", "饼状图": "pie", "pie_chart": "pie", "环形图": "pie",
    "散点图": "scatter", "scatter_chart": "scatter",
    "热力图": "heatmap", "热图": "heatmap", "heat_map": "heatmap",
    "箱线图": "boxplot", "箱型图": "boxplot", "盒须图": "boxplot", "box_plot": "boxplot",
    "雷达图": "radar", "radar_chart": "radar",
    "漏斗图": "funnel", "funnel_chart": "funnel",
}

# 主题配色（跟随前端主色 #4F46E5，多系列时依次取用）
PALETTE = [
    "#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#06B6D4",
    "#8B5CF6", "#EC4899", "#84CC16",
]

SCHEMA = {
    "type": "function",
    "function": {
        "name": "plot_chart",
        "description": (
            "把数据渲染成**可交互图表**显示在对话里（浏览器端 ECharts 渲染，不执行代码，毫秒级返回）。\n"
            "优点：鼠标悬停看数值、点击图例筛选系列、支持导出 PNG，中文显示正常。\n"
            "适合：数据对比（柱状）、趋势（折线）、占比（饼图）、分布（散点/箱线）、相关性（热力图）等。\n"
            "**使用流程（重要）**：\n"
            "1. 数据在用户上传的文件里 → 先用 run_python 读取文件并把数据 print 成 JSON，"
            "再把数据传给本工具画图（本工具不读文件、不做计算）。\n"
            "2. 数据需要统计/聚合 → 先用 run_python 算出结果，再把结果传给本工具。\n"
            "3. 数据已直接给出（用户消息里的数字）→ 直接调用本工具。\n"
            "**数据必须是真实数据（用户提供或 run_python 的计算结果），不要凭记忆编造数值。**\n"
            "如果需要的是图片文件（用户明确说要导出、放进报告、下载），那应该用 run_python 画图。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": list(SUPPORTED_TYPES.keys()),
                    "description": "图表类型",
                },
                "series": {
                    "type": "array",
                    "description": (
                        "数据系列列表，每个元素形如 {\"name\": \"系列名\", \"data\": [...]}。\n"
                        "bar / line / grouped_bar / boxplot：data 为数值数组（boxplot 每项是 [min,Q1,中位数,Q3,max]）\n"
                        "pie / funnel：data 为 [{\"name\":\"苹果\",\"value\":30}, ...]\n"
                        "scatter：data 为 [[x, y], ...]\n"
                        "heatmap：data 为 [[x索引, y索引, 数值], ...]\n"
                        "radar：data 为数值数组，长度与 categories 一致"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "系列名称（单系列可省略）"},
                            "data": {"type": "array", "items": {}, "description": "数据数组"},
                        },
                        "required": ["data"],
                    },
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "类目轴标签（如月份、城市名）。bar / line / grouped_bar / radar 需要；饼图可不填。",
                },
                "title": {"type": "string", "description": "图表标题（可选，建议简短）"},
                "x_label": {"type": "string", "description": "X 轴名称（可选）"},
                "y_label": {"type": "string", "description": "Y 轴名称（可选）"},
                "unit": {"type": "string", "description": "数值单位（可选），如 元 / 人 / %，会显示在提示框里"},
                "stack": {"type": "boolean", "description": "bar / line 是否堆叠显示，默认 false"},
            },
            "required": ["chart_type", "series"],
        },
    },
}


# ================================================================
# 参数清洗
# ================================================================

def _to_number(value):
    """把各种形式的数值转成 float；转不了返回 None"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "").replace("%", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _first_number(value):
    """
    从「记录型」数据里取出数值。

    背景：模型有时不直接给数字，而是给一整行记录，例如
        {"城市等级": "1", "用户数量": 0.5}
    这种情况下要把其中的数值字段（0.5）抽出来，否则整条数据会被当成"非数值"丢掉，
    最终画出一张只有坐标轴、没有柱子的空图。
    """
    if isinstance(value, dict):
        # 常见布局：{"name": "M", "value": 12} → 优先取 value
        if "value" in value:
            n = _to_number(value["value"])
            if n is not None:
                return n
        # 否则收集所有能转成数字的字段，取「最后一个」
        # 之所以取最后一个：模型输出记录时习惯把「维度」放前面、「数值」放后面，
        # 例如 {"城市等级": "1", "用户数量": 0.5} —— 我们要的是 0.5 而不是 1。
        candidates = []
        for k, v in value.items():
            if str(k).lower() in ("name", "label", "名称", "类别", "类目", "维度"):
                continue
            n = _to_number(v)
            if n is not None:
                candidates.append(n)
        if candidates:
            return candidates[-1]
        return None
    return _to_number(value)


def _clean_categories(categories):
    """类目轴：统一转成字符串列表"""
    if not categories:
        return []
    if isinstance(categories, str):
        categories = [categories]
    return [str(c).strip() for c in categories][:MAX_CATEGORIES]


def _clean_series(series):
    """
    清洗系列数据。返回 (series_list, warnings)

    支持三种形态：
    - [{"name": "销量", "data": [1,2,3]}]          标准形态
    - [{"name": "销量", "data": [{"name":"A","value":1}]}]  饼图形态
    - [[1,2,3]]                                     缺省 name 的简写
    """
    warnings = []
    if not isinstance(series, list):
        return [], ["series 必须是数组"]

    cleaned = []
    for idx, item in enumerate(series[:MAX_SERIES]):
        name = f"系列{idx + 1}"
        raw_data = None

        if isinstance(item, dict):
            name = str(item.get("name") or name)
            raw_data = item.get("data")
        elif isinstance(item, list):
            raw_data = item

        if raw_data is None:
            warnings.append(f"系列「{name}」缺少 data，已跳过")
            continue

        # 容错：模型可能把 data 写成对象映射 {"1月": 120, "2月": 200}
        if isinstance(raw_data, dict):
            raw_data = list(raw_data.values())

        if not isinstance(raw_data, list):
            warnings.append(f"系列「{name}」的 data 不是数组，已跳过")
            continue

        # 形态 A：饼图 / 漏斗图 —— [{name, value}, ...]
        if raw_data and isinstance(raw_data[0], dict) and "value" in raw_data[0]:
            points = []
            for p in raw_data[:MAX_CATEGORIES]:
                v = _to_number(p.get("value"))
                if v is None:
                    continue
                points.append({"name": str(p.get("name", "")), "value": v})
            if points:
                cleaned.append({"name": name, "data": points})
            continue

        # 形态 B：散点 / 热力 —— [[x, y], ...] 或 [[x, y, v], ...]
        if raw_data and isinstance(raw_data[0], list):
            points = []
            for p in raw_data[:MAX_POINTS]:
                if not isinstance(p, list):
                    continue
                nums = [_to_number(x) for x in p]
                if any(n is None for n in nums):
                    continue
                points.append(nums)
            if points:
                cleaned.append({"name": name, "data": points})
            continue

        # 形态 C：普通一维数值数组（柱状/折线/箱线/雷达）
        # 兼容「记录型」数据：[{"城市等级":"1","用户数量":0.5}, ...] → 取其中的数值字段
        points = []
        for v in raw_data[:MAX_POINTS]:
            if isinstance(v, list):                      # 箱线图 [min,Q1,med,Q3,max]
                nums = [_to_number(x) for x in v]
                if any(n is None for n in nums):
                    continue
                points.append(nums)
            else:
                n = _first_number(v)
                if n is None:
                    continue                                  # 非数值直接丢弃（避免 null 破图）
                points.append(n)
        if points:
            cleaned.append({"name": name, "data": points})

    return cleaned, warnings


def _normalize_type(chart_type):
    """把各种写法归一化到标准类型"""
    if not chart_type:
        return "bar"
    t = str(chart_type).strip().lower().replace(" ", "_").replace("-", "_")
    if t in SUPPORTED_TYPES:
        return t
    return TYPE_ALIASES.get(t) or TYPE_ALIASES.get(str(chart_type).strip()) or "bar"


# ================================================================
# 组装 ECharts option
# ================================================================

def _base_option(title, x_label, y_label):
    """所有图表共用的基础配置（统一风格）"""
    option = {
        "color": PALETTE,
        "animation": True,
        "animationDuration": 500,
    }
    if title:
        option["title"] = {
            "text": str(title)[:60],
            "left": "center",
            "textStyle": {"fontSize": 15, "fontWeight": "normal"},
        }
    if x_label or y_label:
        option["grid"] = {"left": 48, "right": 24, "top": 56 if title else 32, "bottom": 44}
        option["xAxis"] = {"name": str(x_label or "")}
        option["yAxis"] = {"name": str(y_label or "")}
    return option


def _finalize_layout(option: dict) -> dict:
    """
    统一排版，修掉「图例压住标题」的问题。

    之前标题和图例都挤在顶部（title 居中 + legend top:4），多系列时必然重叠。
    现在改成：标题固定左上角、图例统一沉到底部，并给绘图区留出上下留白。
    """
    has_title = bool(option.get("title"))
    if has_title:
        t = option["title"]
        text = t.get("text") if isinstance(t, dict) else str(t)
        option["title"] = {
            "text": text,
            "left": 0,
            "top": 0,
            "textStyle": {"fontSize": 14, "fontWeight": "normal"},
        }

    has_legend = bool(option.get("legend"))
    if has_legend:
        legend = option["legend"] if isinstance(option["legend"], dict) else {}
        legend.pop("top", None)
        legend["bottom"] = 4
        legend["type"] = "scroll"
        option["legend"] = legend

    grid = option.get("grid") if isinstance(option.get("grid"), dict) else {}
    grid["left"] = grid.get("left", 48)
    grid["right"] = grid.get("right", 24)
    grid["top"] = 46 if has_title else 26
    grid["bottom"] = 62 if has_legend else 42
    option["grid"] = grid

    return option


def build_chart(chart_type, series, categories=None, title=None,
                x_label=None, y_label=None, unit=None, stack=False):
    """
    把模型给的参数组装成一份标准 ECharts option。

    返回：
        {"success": True, "output": "...", "charts": [{"type","option","title"}]}
        或 {"success": False, "error": "..."}
    """
    ctype = _normalize_type(chart_type)
    cats = _clean_categories(categories)
    series_list, warnings = _clean_series(series)

    if not series_list:
        return {"success": False, "error": "没有可用的数据：series 为空或数值无法解析"}

    unit = (str(unit).strip() if unit else "")
    option = _base_option(title, x_label, y_label)
    option["tooltip"] = {"trigger": "item" if ctype in ("pie", "funnel") else "axis"}

    # ---------------- 柱状 / 折线 / 分组柱 ----------------
    if ctype in ("bar", "line", "grouped_bar"):
        length = max(len(s["data"]) for s in series_list)
        if not cats:
            cats = [str(i + 1) for i in range(length)]
        option["xAxis"] = {"type": "category", "data": cats[:max(len(cats), length)],
                           "axisLabel": {"interval": 0, "rotate": 30 if len(cats) > 8 else 0}}
        option["yAxis"] = {"type": "value", "name": str(y_label or "")}
        if x_label:
            option["xAxis"]["name"] = str(x_label)
        if stack:
            option["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "shadow"}}
        option["series"] = []
        for s in series_list:
            item = {
                "name": s["name"],
                "type": "bar" if ctype in ("bar", "grouped_bar") else "line",
                "data": s["data"],
                "smooth": ctype == "line",
                "symbolSize": 6,
            }
            if stack:
                item["stack"] = "total"
            if ctype == "bar":
                item["itemStyle"] = {"borderRadius": [4, 4, 0, 0]}
            option["series"].append(item)
        if len(series_list) > 1:
            option["legend"] = {"top": 4, "type": "scroll"}

    # ---------------- 饼图 ----------------
    elif ctype == "pie":
        data = series_list[0]["data"]
        if cats and data and isinstance(data[0], (int, float)):
            data = [{"name": cats[i] if i < len(cats) else str(i + 1), "value": v}
                    for i, v in enumerate(data)]
        option["series"] = [{
            "name": title or "占比",
            "type": "pie",
            "radius": ["40%", "68%"],
            "center": ["50%", "46%"],   # 上移一点，给底部图例留位置
            "data": data,
            "label": {"formatter": "{b}: {c}" + (f" {unit}" if unit else "") + " ({d}%)"},
            "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowColor": "rgba(0,0,0,.2)"}},
        }]
        option["legend"] = {"bottom": 0, "type": "scroll"}
        option["tooltip"] = {"trigger": "item"}

    # ---------------- 散点图 ----------------
    elif ctype == "scatter":
        option["xAxis"] = {"type": "value", "name": str(x_label or "")}
        option["yAxis"] = {"type": "value", "name": str(y_label or "")}
        option["series"] = [{"name": s["name"], "type": "scatter", "data": s["data"],
                             "symbolSize": 9} for s in series_list]
        if len(series_list) > 1:
            option["legend"] = {"top": 4}

    # ---------------- 热力图 ----------------
    elif ctype == "heatmap":
        points = series_list[0]["data"]
        x_len = int(max((p[0] for p in points), default=0)) + 1
        y_len = int(max((p[1] for p in points), default=0)) + 1
        x_cats = cats[:x_len] if cats else [str(i) for i in range(x_len)]
        y_cats = [s["name"] for s in series_list] if len(series_list) > 1 else [str(i) for i in range(y_len)]
        values = [p[2] for p in points if len(p) > 2]
        option["xAxis"] = {"type": "category", "data": x_cats}
        option["yAxis"] = {"type": "category", "data": y_cats[:y_len] if len(y_cats) >= y_len else [str(i) for i in range(y_len)]}
        option["visualMap"] = {"min": min(values) if values else 0,
                               "max": max(values) if values else 1,
                               "calculable": True, "orient": "horizontal", "left": "center", "bottom": 0}
        option["series"] = [{"name": title or "热力", "type": "heatmap",
                             "data": points, "label": {"show": len(points) <= 60}}]
        option["tooltip"] = {"trigger": "item"}

    # ---------------- 箱线图 ----------------
    elif ctype == "boxplot":
        if not cats:
            cats = [f"组{i + 1}" for i in range(len(series_list[0]["data"]))]
        option["xAxis"] = {"type": "category", "data": cats}
        option["yAxis"] = {"type": "value", "name": str(unit or "")}
        option["series"] = [{"name": s["name"] or "分布", "type": "boxplot",
                             "data": s["data"]} for s in series_list]
        option["tooltip"] = {"trigger": "item"}

    # ---------------- 雷达图 ----------------
    elif ctype == "radar":
        data0 = series_list[0]["data"]
        dims = cats if cats else [f"维度{i + 1}" for i in range(len(data0))]
        max_val = 1
        for s in series_list:
            for v in s["data"]:
                if isinstance(v, (int, float)) and v > max_val:
                    max_val = v
        option["radar"] = {
            "indicator": [{"name": d, "max": max_val * 1.1} for d in dims[:len(data0)]],
            "radius": "62%",
        }
        option["series"] = [{"type": "radar", "data": [
            {"name": s["name"], "value": s["data"]} for s in series_list]}]
        if len(series_list) > 1:
            option["legend"] = {"bottom": 0, "type": "scroll"}
        option["tooltip"] = {"trigger": "item"}

    # ---------------- 漏斗图 ----------------
    elif ctype == "funnel":
        data = series_list[0]["data"]
        if cats and data and isinstance(data[0], (int, float)):
            data = [{"name": cats[i] if i < len(cats) else str(i + 1), "value": v}
                    for i, v in enumerate(data)]
        option["series"] = [{"name": title or "漏斗", "type": "funnel",
                             "left": "10%", "width": "80%", "sort": "descending",
                             "label": {"formatter": "{b}: {c}" + (f" {unit}" if unit else "")},
                             "data": data}]
        option["legend"] = {"bottom": 0, "type": "scroll"}
        option["tooltip"] = {"trigger": "item"}

    # ---------------- 统计摘要（回给模型的文字） ----------------
    # 统一排版（标题左上、图例底部，避免重叠）
    option = _finalize_layout(option)

    total_points = sum(len(s["data"]) for s in series_list)
    parts = [f"已生成{SUPPORTED_TYPES[ctype]}：{len(series_list)} 个系列、{total_points} 个数据点"]
    if cats:
        parts.append(f"类目数 {len(cats)}")
    if warnings:
        parts.append("；".join(warnings))
    summary = "，".join(parts) + "。图表已在用户界面渲染（可交互、可导出 PNG）。"

    return {
        "success": True,
        "output": summary,
        "charts": [{
            "type": ctype,
            "title": str(title or SUPPORTED_TYPES[ctype]),
            "option": option,
        }],
    }


_ALLOWED_ARGS = {"chart_type", "series", "categories", "title",
                 "x_label", "y_label", "unit", "stack"}


def execute(**kwargs):
    """工具入口：dispatch() 会以关键字参数调用。多余的参数会被忽略，避免报错。"""
    try:
        args = {k: v for k, v in kwargs.items() if k in _ALLOWED_ARGS}
        result = build_chart(**args)
        if not result.get("success"):
            logger.warning(f"plot_chart 参数不合法: {result.get('error')}")
        else:
            logger.info(f"📊 已生成图表配置: {result['output']}")
        return result
    except TypeError as e:
        return {"success": False, "error": f"参数错误：{e}"}
    except Exception as e:
        logger.error(f"plot_chart 执行失败: {e}", exc_info=True)
        return {"success": False, "error": f"生成图表失败: {e}"}
