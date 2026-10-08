"""
================================================================
配置模块 — 从环境变量加载所有配置项
================================================================
使用 pydantic-settings 实现类型安全的环境变量校验。
所有敏感信息（API Key 等）通过 .env 文件或系统环境变量注入，
绝不硬编码在代码中。

使用方式：
    from backend.config import settings
    api_key = settings.bailian_api_key
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    应用配置类
    所有字段自动从环境变量或 .env 文件读取
    """

    # ========== 百炼 API 配置 ==========
    # API Key：在阿里云百炼控制台获取
    bailian_api_key: str = ""

    # API 基础地址：百炼的 OpenAI 兼容端点
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 默认模型：qwen-plus 性价比高，也可选 qwen-max / qwen-turbo
    bailian_model: str = "qwen-plus"

    # ========== 绘图策略 ==========
    # 是否允许「服务端绘图」（run_python 里 import matplotlib/seaborn 画图）。
    #
    # 默认 False（禁用），原因：
    #   图表展示统一走 plot_chart 工具（浏览器端 ECharts 渲染，可交互、可导出 PNG），
    #   服务端画图慢、占 CPU、图片不可交互，还容易让模型「该用 plot_chart 时误用 matplotlib」。
    #   禁用后模型一旦尝试 import matplotlib，会收到明确报错并被引导改用 plot_chart。
    #
    # 什么时候需要打开：确实要在服务器上生成图片文件时（如批量出图、报表归档），
    #   在 .env 里写 ENABLE_SERVER_PLOTTING=true 即可恢复。
    enable_server_plotting: bool = False

    # ========== 访问口令（部署到公网时必设） ==========
    # 留空 = 不启用鉴权（本地开发方便）。
    # 设置后，所有 /api/* 业务接口都要求携带有效 token（前端输一次口令即可）。
    # 不设的风险：任何人打开你的网址就能用你的模型额度跑分析、传文件。
    access_password: str = ""

    # ========== 工具调用配置 ==========
    # 单轮对话里「模型请求 + 工具执行」最多循环多少轮。
    # 一次复杂的数据分析通常是「读文件 → 看结构 → 聚合 → 画图」多轮接力，
    # 10 轮偏紧（容易被截断在分析中途），30 轮足够放开手脚。
    # 死循环由下面的 max_same_call_repeat 兜底，不靠这个数字硬扛。
    max_tool_iterations: int = 30

    # 熔断阈值：模型连续发起多少次「完全相同」的工具调用（同名 + 同参数）就停止。
    # 调高会更容易空转烧 token，调低可能误伤「刻意重复执行同一操作」的场景。
    max_same_call_repeat: int = 3

    # ========== 回收站工具配置 ==========
    # clean_recycle_bin 工具只能操作此目录下的文件
    # 提供路径穿越保护的安全边界
    recycle_bin_dir: str = "./tmp-recycle"

    # ========== 日志配置 ==========
    # 日志级别：DEBUG（调试）/ INFO（信息）/ WARNING（警告）/ ERROR（错误）
    log_level: str = "INFO"

    # ========== 服务配置 ==========
    # 后端监听端口
    server_port: int = 8000

    # 后端监听地址（127.0.0.1 = 仅本地，0.0.0.0 = 所有网卡）
    server_host: str = "127.0.0.1"

    class Config:
        """
        Pydantic Settings 配置
        """
        # .env 文件路径：优先读项目根目录的 .env
        env_file = ".env"
        # 即使 .env 文件不存在也不报错（方便 CI/CD 直接用系统环境变量）
        env_file_encoding = "utf-8"


# ========== 全局单例 ==========
# 整个应用通过导入 settings 来获取配置
settings = Settings()
