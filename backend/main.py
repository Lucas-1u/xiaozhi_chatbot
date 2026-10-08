"""
================================================================
FastAPI 应用入口
================================================================
使用 uvicorn 运行：
    uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
"""

import os
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from backend.config import settings
from backend.db import init_db
from backend.services import auth
from backend.routes.chat import router as chat_router
from backend.routes.sessions import router as sessions_router
from backend.routes.memory import router as memory_router
from backend.routes.upload import router as upload_router
from backend.routes.auth import router as auth_router

# ========== 日志配置 ==========
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ========== 创建 FastAPI 应用 ==========
app = FastAPI(
    title="小智 AI 聊天助手",
    description="基于阿里云百炼 LLM API 的智能聊天 Agent，支持多轮对话、工具调用和跨会话记忆",
    version="1.0.0",
)

# ========== CORS 中间件 ==========
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 访问口令守卫 ==========
# 只有 .env 里设了 ACCESS_PASSWORD 才生效；留空时完全放行（本地开发方便）。
# 拦的是 /api/* 的业务接口，静态资源放行 —— 否则连"输入口令"的页面都加载不出来。
@app.middleware("http")
async def access_guard(request: Request, call_next):
    if not auth.auth_enabled():
        return await call_next(request)

    path = request.url.path
    if not auth.is_public_path(path):
        token = request.headers.get("x-access-token", "")
        if not auth.verify_token(token):
            return JSONResponse(
                status_code=401,
                content={"detail": "需要访问口令"},
            )
    return await call_next(request)

# 前端文件目录
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


# ========== 根路径：返回前端 HTML 页面 ==========
@app.get("/")
async def serve_frontend():
    """返回聊天界面 HTML 页面"""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": "前端文件未找到"}


# ========== API 路由（必须在静态文件挂载之前注册） ==========
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(memory_router)
app.include_router(upload_router)


# ========== 健康检查 ==========
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "service": "小智 AI 聊天助手",
        "version": "1.0.0",
    }


# ========== 启动事件 ==========
@app.on_event("startup")
async def on_startup():
    logger.info("=" * 50)
    logger.info("🚀 小智 AI 聊天助手 启动中...")
    logger.info(f"   模型: {settings.bailian_model}")
    logger.info(f"   API: {settings.bailian_base_url}")
    logger.info(f"   端口: {settings.server_port}")
    logger.info(
        "   访问口令: " + ("已启用 ✅" if auth.auth_enabled()
                       else "未设置 ⚠️（公网部署请务必在 .env 里设 ACCESS_PASSWORD）")
    )
    logger.info("=" * 50)
    await init_db()


# ========== 挂载静态资源目录 ==========
# 分别挂载 css 和 js 子目录，避免 Mount("/") 吞掉 API 路由
css_dir = os.path.join(FRONTEND_DIR, "css")
js_dir = os.path.join(FRONTEND_DIR, "js")
plots_dir = os.path.join(FRONTEND_DIR, "plots")

if os.path.isdir(css_dir):
    app.mount("/css", StaticFiles(directory=css_dir), name="css")
if os.path.isdir(js_dir):
    app.mount("/js", StaticFiles(directory=js_dir), name="js")
os.makedirs(plots_dir, exist_ok=True)
app.mount("/plots", StaticFiles(directory=plots_dir), name="plots")
