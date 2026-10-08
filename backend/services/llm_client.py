"""
================================================================
百炼 LLM 客户端 — llm_client.py
================================================================
封装阿里云百炼大模型 API 的调用逻辑。
百炼提供 OpenAI 兼容接口，所以可以直接使用 openai 库。

核心职责：
1. 创建 OpenAI 客户端（指向百炼的 API 地址）
2. 封装流式聊天补全请求（stream=True）
3. 处理 API 错误并转为友好异常

使用方式：
    from backend.services.llm_client import LLMClient
    client = LLMClient()
    async for chunk in client.chat_completion_stream(messages, tools=None):
        print(chunk)
"""

import logging
import httpx
from openai import AsyncOpenAI
from backend.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """
    百炼 LLM API 客户端

    使用 AsyncOpenAI（异步客户端）以支持 FastAPI 的异步特性。
    流式请求通过 async for 逐个产出 delta chunk。
    """

    def __init__(self):
        """
        初始化客户端
        API Key 和 Base URL 从全局配置 settings 中读取（环境变量注入）

        注意：使用 verify=False 是为了解决 Windows Python SSL 证书问题。
        部署到 Linux 服务器后可以移除这个设置。
        """
        # 创建不验证 SSL 证书的 HTTP 客户端（Windows 开发环境需要）
        http_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

        self.client = AsyncOpenAI(
            # 百炼的 API Key
            api_key=settings.bailian_api_key,
            # 指向百炼的 OpenAI 兼容端点
            # 例如：https://dashscope.aliyuncs.com/compatible-mode/v1
            base_url=settings.bailian_base_url,
            # 注入自定义 HTTP 客户端（跳过 SSL 验证）
            http_client=http_client,
        )
        # 默认模型名称：qwen-plus（性价比高）
        self.model = settings.bailian_model
        logger.info(f"百炼客户端初始化完成，模型: {self.model}")

    async def chat_completion_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        enable_search: bool = False,
        enable_thinking: bool = False,
    ):
        """
        发起流式聊天补全请求

        这是一个异步生成器（async generator），每次 yield 一个 delta chunk。
        chunk 的结构取决于百炼返回的内容：
        - 普通文本：chunk.choices[0].delta.content 有值
        - 工具调用：chunk.choices[0].delta.tool_calls 有值
        - 结束标记：chunk.choices[0].finish_reason 有值

        参数：
            messages: 对话消息列表，格式为 [{"role": "user", "content": "..."}, ...]
            tools: 可选的工具定义列表，格式符合 OpenAI function calling 规范
            temperature: 生成温度（0-2），越高越随机
            enable_search: 是否启用联网搜索（默认 False）。
                开启后模型会先检索实时网页再回答，适合天气、新闻、股价等
                时效性问题。注意：该功能按次额外计费，且首 token 延迟会增加
                （必须先搜索再生成）。
            enable_thinking: 是否启用深度思考（默认 False）。
                开启后模型会先生成长篇内部推理再作答，数学、逻辑、多步骤
                规划这类复杂问题的准确率更高，但首 token 延迟会从约 0.6 秒
                升到 5-6 秒。

        产出：
            ChatCompletionChunk 对象，逐个产出

        异常：
            LLMClientError：API 调用失败时抛出
        """
        try:
            # extra_body 承载百炼的「非 OpenAI 标准」参数
            #
            # 思考模式的处理（踩过坑，别改回"总是传布尔值"）：
            # - 开启时显式传 true；
            # - 关闭时【不传这个参数】，让模型用自己的默认值。
            #   原因：百炼部分模型（如 qwen3.7-max-preview）是"只支持思考模式"的，
            #   一旦显式传 enable_thinking=false，接口会直接 400：
            #   "The value of the enable_thinking parameter is restricted to True."
            extra_body: dict = {}
            if enable_thinking:
                extra_body["enable_thinking"] = True

            # 联网搜索：开启后模型会先检索实时网页再回答。
            # 关闭时完全不传该参数，与改动前的请求保持一致。
            if enable_search:
                extra_body["enable_search"] = True

            # 构建请求参数
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,  # 启用流式输出
                # stream_options 用于在流式响应中包含 usage 信息
                "stream_options": {"include_usage": True},
                "extra_body": extra_body,
            }

            # 如果有工具定义，加入请求
            if tools:
                kwargs["tools"] = tools
                # tool_choice="auto" 表示让模型自己决定是否调用工具
                kwargs["tool_choice"] = "auto"

            # 判断是否属于「该模型只能开思考模式」的报错
            def _needs_thinking(err: Exception) -> bool:
                msg = str(err)
                return ("enable_thinking" in msg) and ("restricted to True" in msg)

            # 发起流式请求
            try:
                stream = await self.client.chat.completions.create(**kwargs)
            except Exception as req_err:
                if (not enable_thinking) and _needs_thinking(req_err):
                    # 兜底：换用只支持思考模式的模型时，前端开关是关的，
                    # 这里自动补上 enable_thinking=True 重试一次，避免功能直接不可用。
                    logger.warning("当前模型只支持思考模式，自动以 enable_thinking=True 重试")
                    kwargs["extra_body"]["enable_thinking"] = True
                    stream = await self.client.chat.completions.create(**kwargs)
                else:
                    raise

            # 逐个产出 delta chunk
            yielded_any = False
            try:
                async for chunk in stream:
                    yielded_any = True
                    yield chunk
            except Exception as stream_err:
                # 同类错误也可能在开始读取流时才抛出（此时还没吐过任何内容，可以安全重试）
                if (not yielded_any) and (not enable_thinking) and _needs_thinking(stream_err):
                    logger.warning("当前模型只支持思考模式，自动以 enable_thinking=True 重试")
                    kwargs["extra_body"]["enable_thinking"] = True
                    stream2 = await self.client.chat.completions.create(**kwargs)
                    async for chunk in stream2:
                        yield chunk
                else:
                    raise

        except Exception as e:
            # 捕获并包装所有异常
            logger.error(f"百炼 API 调用失败: {e}")
            raise LLMClientError(f"LLM API 调用失败: {str(e)}") from e


class LLMClientError(Exception):
    """
    LLM 客户端自定义异常
    用于区分网络错误、API 错误和业务逻辑错误
    """
    pass
