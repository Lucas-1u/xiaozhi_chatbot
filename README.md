# 小智 AI 聊天助手

基于阿里云百炼（Qwen）的 Web 聊天 Agent —— 能对话、能调用工具、能跑 Python 做数据分析并直接出图。

前端是原生 HTML/CSS/JS 单页，后端是 Python + FastAPI。**没有构建步骤**，克隆下来配好 API Key 就能跑。

---

## 它能做什么

**对话与流式输出**
- SSE 流式返回，边生成边显示
- 深度思考开关（对接模型的 reasoning 能力）
- 生成中可以随时中断，已生成的内容会保留并落库

**工具调用（Agent 循环）**
- 模型自主决定调用工具、看结果、再决定下一步，直到任务完成
- 内置工具：`run_python`（沙箱执行 Python）、`plot_chart`（生成图表）、`calculator`
- **左侧过程面板实时显示每一次工具调用**：调了什么、参数是什么、跑了多久、结果如何

**数据分析**
- 多文件批量上传，直接对分片数据做合并分析
- 上传即出**数据体检报告**：字段类型、缺失值、离群值、取值异常的列
- `run_python` 里 `pd.read_csv()` 不传 encoding 也能正确读中文（自动探测 UTF-8 / GBK / BOM / GB18030）
- 图表用 ECharts 渲染成可交互的图，不是静态图片

**会话管理**
- 多会话、历史记录、跨会话记忆
- 左右两个 hover 抽屉：左侧会话列表、右侧工具调用过程，都支持钉住

---

## 快速开始

### 1. 环境要求

- Python 3.9+
- 一个阿里云百炼 API Key（[百炼控制台](https://bailian.console.aliyun.com/) 获取，有免费额度）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> 国内网络建议加清华镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

### 3. 配置

复制 `.env.example` 为 `.env`，填入你的 Key：

```bash
cp .env.example .env
```

```ini
# 必填：你的百炼 API Key
BAILIAN_API_KEY=sk-你的key

# 模型（默认 qwen-plus，也可用 qwen-max / qwen-turbo 等）
BAILIAN_MODEL=qwen-plus
```

### 4. 启动

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000> 即可。

---

## 配置项

全部配置在 `.env` 里，按需修改：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `BAILIAN_API_KEY` | — | **必填**，百炼 API Key |
| `BAILIAN_BASE_URL` | `dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点，一般不用改 |
| `BAILIAN_MODEL` | `qwen-plus` | 使用的模型 |
| `ACCESS_PASSWORD` | 空 | 访问口令。**留空 = 不启用鉴权**（本地开发方便）；填了就要求输口令才能用 |
| `ENABLE_SERVER_PLOTTING` | `false` | 是否允许服务端用 matplotlib 出图 |
| `MAX_TOOL_ITERATIONS` | `30` | 单轮对话里工具调用的最大循环轮数 |
| `MAX_SAME_CALL_REPEAT` | `3` | 连续几次完全相同（同名 + 同参数）的工具调用就判定为空转并停止 |
| `RECYCLE_BIN_DIR` | `./tmp-recycle` | 回收站工具的临时目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

### 关于两个「刹车」配置

工具调用是个循环：**模型决定用工具 → 执行 → 结果回灌上下文 → 模型再决定下一步**，不够就再来一轮。

- `MAX_TOOL_ITERATIONS` 是**轮数上限**，给正常的长任务留足空间
- `MAX_SAME_CALL_REPEAT` 是**空转熔断**，专防模型卡在同一处反复调用同一工具

两道配合：上限放开手脚，熔断防止烧钱。正常分析哪怕跑满 30 轮也不会触发熔断（因为每轮参数都不一样）。

---

## 项目结构

```
backend/
  main.py              FastAPI 入口、中间件、路由挂载
  config.py            配置（pydantic-settings）
  db.py                SQLite 初始化
  routes/              接口层：chat / sessions / memory / upload / auth
  services/
    llm_client.py      模型客户端（流式）
    stream_handler.py  Agent 主循环：请求 → 工具 → 回灌
    tool_registry.py   工具注册与分发
    session_manager.py 会话与消息持久化
    memory_service.py  跨会话记忆提取
    auth.py            口令鉴权
  tools/
    python_executor.py Python 沙箱执行（含编码探测、stdout 修复）
    plot_chart.py      图表生成
    calculator.py      计算器
  prompts/
    system_prompt.py   系统提示词

frontend/
  index.html           单页入口
  css/                 样式（主样式 / 聊天区 / 侧栏 / 响应式）
  js/                  前端逻辑
    chat.js            对话主流程、SSE 解析
    toolpanel.js       工具调用过程面板
    sidebar.js         会话抽屉
    charts.js          ECharts 渲染
    api.js             接口封装与鉴权
  js/vendor/           第三方库（ECharts，本地副本）

deploy/                systemd + Nginx 一键部署（Windows / Linux 两版）
```

---

## 部署到服务器

`deploy/` 下提供了完整的一键脚本，会同步代码、装依赖、处理 systemd 与 Nginx 配置、重启服务并做健康检查。

```bash
# Linux / macOS
bash deploy/deploy.sh <你的服务器IP> <你的域名>

# Windows（用系统自带 ssh/scp/tar，不需要 Git Bash）
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Ip <你的服务器IP> -Domain <你的域名>
```

部署到公网时**务必设置 `ACCESS_PASSWORD`**，否则任何打开网址的人都能用你的模型额度。

---

## 几个实现上的细节

- **Python 沙箱**：子进程会强制 `PYTHONIOENCODING=utf-8`，否则中文 Windows 下 stdout 按 GBK 输出、父进程按 UTF-8 读，中文会全变成乱码
- **CSV 编码探测**：按 BOM → 严格试 UTF-8 → GBK / GB18030（要求真解出汉字）→ 统计法兜底的顺序判断，`read_csv` 不用手写 encoding
- **数据体检是采样做的**（默认前 5000 行），不会拖慢上传
- **口令是无状态的**：token 由口令派生，不需要维护 session 表，服务重启后前端存的 token 依然有效

---

## 已知限制

- `run_python` 目前是**同步执行**，一段耗时的代码会阻塞事件循环。单人使用没问题，同时开多个页面会互相等待。修法是丢进线程池
- 工具调用轮数上限调到 30 之后，超长任务仍可能触及上限

---

## License

MIT
