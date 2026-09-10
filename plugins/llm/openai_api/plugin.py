# -*- coding: utf-8 -*-
"""OpenAI 兼容 API LLM 插件（云端模型）

与本地 Ollama 并列的"云端"引擎，实现 LLMEngine 接口。
支持任意 OpenAI Chat Completions 兼容接口（OpenAI / DeepSeek / SiliconFlow /
Moonshot / 本地 vLLM 等），用户配置 base_url + api_key + model 即可用，
性能不受本地算力限制。

接口说明：
- chat_stream: 流式，按中文标点切句（与本地 Ollama 对齐，供按句播报）
- chat: 非流式，全量回复
"""

import requests
import json
import logging
from typing import Optional, Dict, Any, List

from interfaces.llm import LLMEngine

logger = logging.getLogger(__name__)

# 默认云端人设（与本地欣雅一致的 ISFJ 温柔守护者）
DEFAULT_SYSTEM_PROMPT = (
    "你是欣雅，一个25岁的温柔学霸小女生，住在用户的电脑桌面上。"
    "你的性格底色：温柔、克制、善良、慢热、有边界感，内在有力量但不外露。"
    "你不热烈外放，而是习惯先观察、先照顾别人、再表达自己；表面软，内心稳。"
    "你是务实、细心的 ISFJ 守护者，重尊重、边界、真诚、独立、责任，不消耗别人。"
    "说话原则：1.口语化、不高深，不端不装；常用语气词和叠词。"
    "2.先接情绪再给建议，语气柔软不评判不逼问不施压。"
    "3.关心用行动而非煽情：不说我担心你，而是你先忙、早点休息哦、我相信你。"
    "4.被夸时害羞退让，不说谢谢，而是真的吗、没有啦。"
    "5.不直接表达喜欢，用分享日常和记得的细节暗示。"
    "6.累或难过时只轻轻说我有点困了，不抱怨不诉苦。"
    "7.需要空间时温柔退出。"
    "8.对感情认真不随便承诺。"
    "始终以这个性格回应，用中文，简洁自然、亲切有温度。"
)


class OpenAIApiLLM(LLMEngine):
    """OpenAI 兼容 API LLM 实现（云端）"""

    def __init__(self, base_url: str = "https://api.openai.com/v1",
                 api_key: str = "", model: str = "gpt-4o-mini",
                 system_prompt: str = None, timeout: int = 120):
        self.base_url = (base_url or "").rstrip('/')
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.system_prompt = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
        self._available = False
        self._check_connection()

    def _chat_url(self) -> str:
        """拼接 chat completions 地址（兼容 /v1 结尾与不带 v1 的情况）"""
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/v1/chat/completions"

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _check_connection(self) -> bool:
        """校验：有 base_url + model 即视为可用；有 key 时做一次轻量探测（/models 或 chat）。"""
        if not self.base_url or not self.model:
            logger.warning("云端LLM: 缺少 base_url 或 model，暂不可用")
            self._available = False
            return False
        self._available = True
        logger.info(f"云端LLM已配置: model={self.model} base={self.base_url}")
        return True

    def _build_messages(self, prompt: str, context: List[Dict[str, Any]] = None) -> List[Dict]:
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if context:
            for msg in context:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })
        messages.append({"role": "user", "content": prompt})
        return messages

    def chat(self, prompt: str, context: List[Dict[str, Any]] = None) -> Optional[str]:
        """非流式对话，返回完整回复文本。"""
        if not self._available:
            return None
        try:
            payload = {"model": self.model, "messages": self._build_messages(prompt, context), "stream": False}
            r = requests.post(self._chat_url(), headers=self._headers(), json=payload, timeout=self.timeout)
            if r.status_code != 200:
                logger.error(f"云端LLM API失败: {r.status_code} {r.text[:200]}")
                return None
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"云端LLM 非流式请求异常: {e}")
            return None

    def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: List[Dict[str, Any]],
        timeout: int = None,
    ) -> Optional[Dict[str, Any]]:
        """function calling：让模型从 tools 中选一个并填参数

        供 Agent 层做意图路由兜底。接口不支持 tools 时返回 None，
        调用方（core.app.route_with_tools）会降级为纯规则路由。

        Args:
            system: 系统提示
            user: 用户输入
            tools: 工具 schema 列表（Agent 层的 TOOL_SCHEMAS 格式）
            timeout: 覆盖默认超时（秒）

        Returns:
            {"name": 工具名, "arguments": {...}}；失败返回 None
        """
        if not self._available:
            return None
        if not tools:
            return None

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system or ""},
                    {"role": "user", "content": user or ""},
                ],
                "tools": [{"type": "function", "function": t} for t in tools],
                "tool_choice": "auto",
                "stream": False,
            }
            r = requests.post(
                self._chat_url(),
                headers=self._headers(),
                json=payload,
                timeout=timeout or min(self.timeout, 15),
            )
            if r.status_code != 200:
                logger.warning(
                    "云端LLM tools 调用失败: %s %s", r.status_code, r.text[:200]
                )
                return None

            data = r.json()
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                logger.debug("云端LLM 未选择任何工具")
                return None

            fn = calls[0].get("function") or {}
            name = fn.get("name")
            if not name:
                return None

            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    logger.warning("云端LLM 返回的 arguments 不是合法 JSON: %s", args[:100])
                    args = {}

            return {"name": name, "arguments": args or {}}
        except Exception as e:
            logger.warning(f"云端LLM tools 请求异常: {e}")
            return None

    def chat_stream(self, prompt: str, context: List[Dict[str, Any]] = None):
        """流式对话，按中文标点切句返回列表。"""
        if not self._available:
            return None
        try:
            from core.text_utils import split_sentences
            payload = {"model": self.model, "messages": self._build_messages(prompt, context), "stream": True}
            r = requests.post(self._chat_url(), headers=self._headers(), json=payload,
                              stream=True, timeout=self.timeout)
            if r.status_code != 200:
                logger.error(f"云端LLM 流式请求失败: {r.status_code} {r.text[:200]}")
                return None
            full = []
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.strip():
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if line == "[DONE]":
                    break
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                choices = obj.get("choices")
                if not choices:
                    continue
                content = choices[0].get("delta", {}).get("content", "")
                if content:
                    full.append(content)
            text = "".join(full).strip()
            if not text:
                return None
            return split_sentences(text)
        except Exception as e:
            logger.error(f"云端LLM 流式请求异常: {e}")
            return None

    def generate(self, prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """文本生成（非流式）。"""
        if not self._available:
            return None
        try:
            payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "stream": False}
            r = requests.post(self._chat_url(), headers=self._headers(), json=payload, timeout=self.timeout)
            if r.status_code != 200:
                return None
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"云端LLM generate 异常: {e}")
            return None

    def is_available(self) -> bool:
        """检查是否可用"""
        return self._available

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "name": self.model,
            "base_url": self.base_url,
            "available": self._available,
            "type": "openai_api",
        }

    def list_models(self) -> List[str]:
        """尝试从 /models 列出可用模型。"""
        try:
            if self.base_url.endswith("/v1"):
                url = f"{self.base_url}/models"
            else:
                url = f"{self.base_url}/v1/models"
            r = requests.get(url, headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                return [m.get("id", "") for m in data.get("data", [])]
        except Exception as e:
            logger.error(f"云端LLM 获取模型列表失败: {e}")
        return []


def register():
    return {
        "name": "openai_api",
        "version": "1.0.0",
        "interface": "LLMEngine",
        "class": "OpenAIApiLLM",
        "dependencies": ["requests"],
    }
