# 小智 AI 聊天助手 — 项目状态

## 项目概述
基于阿里云百炼 Qwen API 的 Web 聊天 Agent。前端原生 HTML/CSS/JS，后端 Python FastAPI。
已部署上线：https://example.com

- 原始 Spec：本地文件（未随仓库分发）
- 规划文件：本地文件（未随仓库分发）

## 当前进度

| Phase | 状态 |
|---|---|
| Phase 0 — 项目脚手架 | ✅ 完成 |
| Phase 1 — LLM 集成 + 流式聊天 | ✅ 完成 |
| Phase 2 — 会话管理 | ✅ 完成 |
| Phase 3 — Function Calling | ✅ 完成 |
| Phase 4 — 跨会话记忆 | ✅ 完成 |
| Phase 5 — UI 打磨 | ⏳ 部分完成 |
| Phase 6 — 部署上线 | ✅ 完成 |
| Phase 7 — 次要任务（本地模型/文档） | ⏳ 待做 |

## 技术栈
- LLM: 阿里云百炼 `qwen3.6-plus`，OpenAI 兼容 API，**已关闭思考模式**（`enable_thinking: False`）
- 后端: Python 3.14 / FastAPI / uvicorn / aiosqlite
- 前端: 原生 HTML + CSS + JS（零框架）
- 图表: **ECharts（浏览器端渲染，本地内置 `js/vendor/echarts.min.js`，不走 CDN）**
- 存储: SQLite（`data/chat.db`），WAL 模式
- 流式: SSE（Server-Sent Events）
- 部署: 阿里云 ECS（Ubuntu, 1.6GB 内存 + 1GB swap）+ Nginx + systemd

## 已实现功能

1. **流式聊天**：`POST /api/chat/{session_id}` → SSE 流，逐 token 返回
2. **会话管理**：创建/列表/切换/删除，SQLite 持久化，按 `user_token` 隔离
3. **多轮对话**：历史消息自动加载（含真实时间戳）
4. **自动标题**：首条消息前 30 字自动设为会话标题
5. **Markdown 渲染**：代码块、分段、粗体等
6. **跨会话记忆**：从对话提取用户事实，新会话自动召回（差异化功能）
7. **数据可视化**：上传 CSV/Excel → 图表在浏览器端渲染（见下方「双工具分工」）

## 图表：双工具分工

这是本项目的核心设计 —— **「算」与「画」分离**：

| 工具 | 职责 | 实现方式 |
|---|---|---|
| `run_python` | 算：统计、筛选、聚合、复杂计算 | subprocess 沙箱执行 Python |
| `plot_chart` | 画：把数据组装成 ECharts 配置 | **只返回 JSON 配置，不执行任何代码** |

**为什么图表走浏览器端**（对比服务器 matplotlib 出图片）：

1. **快** — 毫秒级返回配置，不启子进程、不跑代码
2. **可交互** — 悬停看数值、点图例筛选、缩放、导出 PNG
3. **省服务器** — 画图算力在用户浏览器
4. **更安全** — 完全不执行模型写的代码
5. **中文正常** — 用浏览器字体，没有 matplotlib 字体缺失变方框的问题

**前端渲染**：`charts.js` 接收配置后调 `echarts.setOption()` 渲染（`appendChart(spec)`）。

**支持类型**：bar / grouped_bar / line / pie / scatter / heatmap / boxplot / radar / funnel
**参数上限**：60 个类目、8 个系列、单系列 1000 点（防止模型塞入超大 payload）

### 服务端绘图（可选，默认关闭）

`ENABLE_SERVER_PLOTTING=false`（见 `.env.example`）。关闭时 `run_python` 里 `import matplotlib` 会报错并引导模型改用 `plot_chart`。

需要「服务端生成图片文件」时（批量出图、报表归档）改成 `true` 恢复。恢复后注意服务器需装中文字体（见「已知问题」第 7 条）。

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 健康检查 |
| POST | /api/sessions | 创建会话 |
| GET | /api/sessions | 列出会话（按 user_token 过滤） |
| GET | /api/sessions/{id} | 获取会话（含消息） |
| DELETE | /api/sessions/{id} | 删除会话 |
| POST | /api/chat/{session_id} | 发送消息（SSE 流） |
| POST | /api/upload | 上传数据文件（≤30MB） |
| GET | /api/memory | 列出记忆 |
| DELETE | /api/memory/{id} | 删除记忆 |

## 项目结构

```
D:\Try_agent/
├── .env                    ← API Key 等（不入仓）
├── requirements.txt
├── CLAUDE.md               ← 本文件
├── backend/
│   ├── main.py             ← FastAPI 入口 + 路由注册
│   ├── config.py           ← Pydantic Settings（含 enable_server_plotting）
│   ├── db.py               ← SQLite 初始化（aiosqlite）
│   ├── routes/
│   │   ├── chat.py         ← 聊天 SSE + 记忆检索/提取
│   │   ├── sessions.py     ← 会话 CRUD
│   │   ├── memory.py       ← 记忆管理
│   │   └── upload.py       ← 文件上传
│   ├── services/
│   │   ├── llm_client.py   ← 百炼 API 封装
│   │   ├── session_manager.py ← 会话/消息 CRUD
│   │   ├── memory_service.py  ← 记忆提取/检索
│   │   ├── tool_registry.py   ← 工具注册（run_python + plot_chart）
│   │   └── stream_handler.py  ← SSE 事件 + 工具调用循环
│   ├── tools/
│   │   ├── python_executor.py ← Python 沙箱（算）
│   │   ├── plot_chart.py      ← ECharts 配置生成（画）
│   │   ├── calculator.py      ← 保留未启用
│   │   └── clean_recycle_bin.py ← 保留未启用
│   └── prompts/
│       └── system_prompt.py   ← 人设 + 记忆注入
├── frontend/
│   ├── index.html
│   ├── css/                ← style / chat / sidebar / responsive
│   ├── js/
│   │   ├── charts.js       ← ECharts 渲染
│   │   ├── vendor/echarts.min.js ← 本地内置
│   │   ├── markdown.js / utils.js / api.js / chat.js / sidebar.js / app.js
│   └── plots/              ← 服务端绘图输出（默认关闭时不产生）
├── data/
│   ├── chat.db             ← SQLite 数据库
│   └── uploads/            ← 上传的数据文件
└── deploy/
    ├── deploy.sh           ← 一键部署（安全版：备份 Nginx、健康检查）
    ├── chat-agent.conf     ← Nginx 配置
    └── chat-agent.service  ← systemd 配置
```

## 已知问题与解决

1. **Windows SSL 证书**：`llm_client.py` 用 `httpx.AsyncClient(verify=False)` 规避
2. **pip 编码**：`requirements.txt` 注释必须纯 ASCII
3. **前端编码**：FastAPI 请求体用 Pydantic 模型解析，不要手动 `request.json()`
4. **SSE 解析**：JS 端按 `\n\n` 分割，不能用单 `\n`
5. **AI 气泡不显示**：`createMessageBubble` 后必须 `appendChild`
6. **沙箱解释器**：必须用 `sys.executable`，用 `python3` 会找不到 venv 里的包
7. **中文字体**（仅服务端绘图时）：服务器装 `fonts-wqy-zenhei`，且代码层强制覆盖 LLM 的字体设置
8. **速度优化**：Qwen3 默认开思考模式，首 token 要 6.6s；加 `enable_thinking: False` 后降到 0.6s

## 启动方式

本地开发：
```bash
cd D:\Try_agent
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
桌面快捷方式：`启动小智AI.bat`（自动挑选装了依赖的 Python 解释器）

部署：
```bash
bash deploy/deploy.sh YOUR_SERVER_IP example.com
```
该脚本会：同步代码 → 装依赖（清华镜像）→ 检查 systemd（无变化则跳过）→ **Nginx 配置安全处理（不覆盖 certbot 的改动，先备份）** → 重启服务 → 健康检查。

## 下一步

1. 下载清洗后的数据（目前只能看统计，无法下载文件）
2. Phase 7：本地小模型部署演示（Ollama）
3. Phase 7：`walkthrough.md` 解释部署/微调/训练三概念
4. README 完善
