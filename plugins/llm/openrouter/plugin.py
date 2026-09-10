"""
通用 OpenAI 兼容 LLM 插件
支持多个免费/付费 LLM 提供商（OpenAI Chat Completions API 兼容）

支持的提供商:
  - Google Gemini (免费, 需 API Key)
  - Groq (免费, 超快推理)
  - OpenRouter (免费模型, 需 API Key)
  - SiliconFlow (免费模型, 需实名认证)
  - 任何 OpenAI 兼容 API
"""

import json
import logging
import os
from typing import Optional, List, Dict, Any

from interfaces.llm import LLMEngine

logger = logging.getLogger(__name__)

# 预设提供商配置
PRESET_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "free_models": [
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
        ],
        "env_key": "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "free_models": [
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-1.5-flash",
        ],
        "env_key": "GOOGLE_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "free_models": [
            "nvidia/nemotron-3-super-120b-a12b:free",
            "google/gemma-4-26b-a4b-it:free",
            "openai/gpt-oss-20b:free",
        ],
        "env_key": "OPENROUTER_API_KEY",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "free_models": [
            "Qwen/Qwen3-8B",
        ],
        "env_key": "SILICONFLOW_API_KEY",
    },
}


class UniversalLLM(LLMEngine):
    """通用 OpenAI 兼容 LLM 实现"""

    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        base_url: str = None,
        provider: str = None,
        system_prompt: str = None,
        #: 单次补全的最大 token 数。
        #:
        #: 默认从 1024 提到 1536 的原因：**规划器要把整个 JSON 计划一次吐完**，
        #: 多步计划 + 中文 description 很容易超过 1024 —— 一旦被截断，
        #: 拿到的是半个 JSON（实测输出停在 `{"command":"for /d %i in ('`），
        #: 解析必然失败，而症状是"规划器偶尔拆不出计划"，看起来像模型不行。
        #: 这是**上限**不是目标值，短回复的延迟与费用不受影响。
        #: 想更保守/更激进都在 `config.yaml` 的 `plugins.llm.params.max_tokens` 调。
        max_tokens: int = 1536,
        temperature: float = 0.7,
        fallback_api_key: str = None,
        fallback_base_url: str = None,
        fallback_model: str = None,
    ):
        """
        初始化通用 LLM

        Args:
            api_key: API Key
            model: 模型名称
            base_url: API 基础 URL
            provider: 预设提供商名称 (groq/gemini/openrouter/siliconflow)
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            fallback_*: 429 限流时自动降级的备用提供商配置
        """
        # 如果指定了预设提供商，使用其配置
        if provider and provider in PRESET_PROVIDERS:
            preset = PRESET_PROVIDERS[provider]
            self.base_url = base_url or preset["base_url"]
            env_key = preset["env_key"]
            self.api_key = api_key or os.environ.get(env_key, "")
            if not model and preset["free_models"]:
                model = preset["free_models"][0]
        else:
            self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

        self.model = model or "gpt-3.5-turbo"
        self.system_prompt = system_prompt or "你是欣雅，一个友善的AI桌面管家。"
        # 备用提供商（429 限流自动降级）
        self.fallback_api_key = fallback_api_key or ""
        self.fallback_base_url = (fallback_base_url or "").rstrip("/")
        self.fallback_model = fallback_model or ""
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._available = False

        self._check_availability()

    def _check_availability(self):
        """检查 API 是否可用"""
        if not self.api_key:
            logger.warning(
                "未设置 API Key。请在 config.yaml 中配置 api_key 或设置对应环境变量。"
            )
            self._available = False
            return

        if len(self.api_key) < 10:
            logger.warning("API Key 格式不正确")
            self._available = False
            return

        self._available = True
        logger.info(f"LLM 初始化成功，模型: {self.model}，Base URL: {self.base_url}")

    def chat(self, prompt: str, context: List[Dict[str, Any]] = None) -> Optional[str]:
        """
        对话生成

        Args:
            prompt: 用户输入
            context: 对话上下文历史

        Returns:
            生成的回复文本，失败返回 None
        """
        if not self._available:
            logger.error("LLM 不可用")
            return None

        try:
            messages = []

            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})

            if context:
                for msg in context:
                    messages.append({"role": msg["role"], "content": msg["content"]})

            messages.append({"role": "user", "content": prompt})

            response = self._call_api(messages)
            return response

        except Exception as e:
            logger.error(f"对话失败: {e}")
            return None

    def generate(self, prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """
        文本生成

        Args:
            prompt: 提示词
            max_tokens: 最大生成 token 数

        Returns:
            生成的文本，失败返回 None
        """
        if not self._available:
            logger.error("LLM 不可用")
            return None

        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ]

            original_max = self.max_tokens
            self.max_tokens = max_tokens
            response = self._call_api(messages)
            self.max_tokens = original_max

            return response

        except Exception as e:
            logger.error(f"生成失败: {e}")
            return None

    def _call_api(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """
        调用 Chat Completions API。
        429 限流时自动降级到备用提供商（fallback_*），一次成功即返回。
        """
        # 优先主提供商
        result = self._request_once(messages, self.base_url, self.api_key, self.model)
        if result is not None:
            return result
        # 主提供商失败（可能是 429/超时）→ 尝试备用
        if self.fallback_api_key and self.fallback_base_url and self.fallback_model:
            logger.warning(f"主提供商失败，降级到备用模型: {self.fallback_model}")
            result = self._request_once(
                messages, self.fallback_base_url, self.fallback_api_key, self.fallback_model
            )
        return result

    def _request_once(self, messages, base_url, api_key, model) -> Optional[str]:
        """向单一端点发起一次请求，成功返回文本，失败返回 None"""
        try:
            import requests

            url = f"{base_url}/chat/completions"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }

            logger.debug(f"调用 API: model={model}, url={url}")
            response = requests.post(url, headers=headers, json=payload, timeout=60)

            if response.status_code != 200:
                logger.error(
                    f"API 错误: {response.status_code} - {response.text[:300]}"
                )
                return None

            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0].get("message", {}).get("content", "")
                if content:
                    logger.info(f"回复成功，长度: {len(content)}")
                    return content.strip()

            logger.warning("API 返回空回复")
            return None

        except Exception as e:
            logger.error(f"API 调用异常: {e}")
            return None

    def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: List[Dict[str, Any]],
        timeout: int = None,
    ) -> Optional[Dict[str, Any]]:
        """function calling：让模型从 tools 中选一个并填参数

        供 Agent 层做意图路由兜底（`core.app.route_with_tools`）。

        **为什么必须提供这个方法**：`core/app.py::route_with_tools` 用
        `hasattr(llm, "chat_with_tools")` 判定能力，缺失时**静默返回 None**。
        本插件原先没有它，于是 `config.yaml` 选 `engine: openrouter` 时
        LLM 路由兜底从未真正生效（设计原则 5「15% 请求走 LLM」名不副实）。
        `openai_api` 插件有此实现，但两者是 `LLMEngine` 的兄弟子类、无继承关系。

        与 `_call_api` 一致地支持 429 限流降级到备用提供商。

        Args:
            system: 系统提示
            user: 用户输入
            tools: 工具 schema 列表（Agent 层 TOOL_SCHEMAS 格式）
            timeout: 覆盖默认超时（秒）；调用方（LLMRouter）不传，此处给 12s
                     兜住"提示词里带 21 个工具 schema"时更长的往返

        Returns:
            {"name": 工具名, "arguments": {...}}；未选工具或失败时返回 None
        """
        if not self._available:
            logger.error("LLM 不可用")
            return None
        if not tools:
            return None

        payload_tools = [{"type": "function", "function": t} for t in tools]
        timeout = timeout or 12

        ok, picked = self._tools_once(
            system, user, payload_tools,
            self.base_url, self.api_key, self.model, timeout,
        )
        if ok:
            # 端点正常响应就返回，**哪怕模型没选工具**（picked 为 None）。
            # 关键：不能用 `if picked is not None` 判定 —— 那会把"模型这次选择
            # 用文字回答"当成端点故障，白打一次备用端点（实测白等 6s）。
            return picked

        if self.fallback_api_key and self.fallback_base_url and self.fallback_model:
            logger.warning(
                f"tools 调用主提供商失败，降级到备用模型: {self.fallback_model}"
            )
            _, picked = self._tools_once(
                system, user, payload_tools,
                self.fallback_base_url, self.fallback_api_key,
                self.fallback_model, timeout,
            )
            return picked
        return None

    def _tools_once(
        self, system, user, payload_tools, base_url, api_key, model, timeout
    ) -> tuple:
        """向单一端点发起一次 tools 请求

        Returns:
            `(endpoint_ok, picked)`：
            - `(True, {...})` 端点正常且模型选了工具
            - `(True, None)`  端点正常但模型本轮未选工具（**不是故障**，不应重试备用）
            - `(False, None)` 端点故障（非 200 / 异常）→ 调用方应尝试备用端点
        """
        try:
            import requests

            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system or ""},
                    {"role": "user", "content": user or ""},
                ],
                "tools": payload_tools,
                "tool_choice": "auto",
                "stream": False,
            }

            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code != 200:
                logger.error(
                    f"tools 调用失败: {response.status_code} - {response.text[:200]}"
                )
                return False, None

            data = response.json()
            choices = data.get("choices") or []
            message = (choices[0].get("message") if choices else None) or {}
            calls = message.get("tool_calls") or []
            if not calls:
                # 推理型模型（如 gpt-oss）在 tool_choice=auto 下常用文字作答，
                # 这是合法结果而非错误 —— 交给上层降级为纯规则路由即可。
                logger.debug("LLM 本轮未选择工具（端点响应正常）")
                return True, None

            fn = calls[0].get("function") or {}
            name = fn.get("name")
            if not name:
                return True, None

            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    logger.warning(
                        f"tools 返回的 arguments 不是合法 JSON: {str(args)[:100]}"
                    )
                    args = {}

            return True, {"name": name, "arguments": args or {}}

        except Exception as e:
            logger.warning(f"tools 请求异常: {e}")
            return False, None

    def is_available(self) -> bool:
        """检查模型是否可用"""
        return self._available

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "name": "universal_llm",
            "model": self.model,
            "version": "1.0.0",
            "base_url": self.base_url,
            "status": "available" if self._available else "unavailable",
        }

    @staticmethod
    def list_providers() -> Dict[str, Any]:
        """列出所有预设提供商"""
        return {
            name: {
                "base_url": info["base_url"],
                "free_models": info["free_models"],
                "env_key": info["env_key"],
            }
            for name, info in PRESET_PROVIDERS.items()
        }


def register():
    return {
        "name": "openrouter",
        "version": "1.0.0",
        "interface": "LLMEngine",
        "class": "UniversalLLM",
        "dependencies": ["requests"],
    }
