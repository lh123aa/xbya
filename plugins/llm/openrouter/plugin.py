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
import time
from typing import Optional, List, Dict, Any

from interfaces.llm import LLMEngine
from plugins.llm.tool_schemas import ToolSchemaError, to_openai_tools

logger = logging.getLogger(__name__)

# 预设提供商配置
#
# ⚠️ **`free_models` 是快照，会腐坏**（P4-B4 实测）：
# 原先这里写的 3 个 Groq 模型**全部已下架**（`llama-3.1-8b-instant` 报 404，
# `gemma2-9b-it` / `mixtral-8x7b-32768` 报 400 "has been decommissioned"），
# OpenRouter 的 `openai/gpt-oss-20b:free` 也已不在免费清单里。
# 后果不是"参数报错"而是**静默替换**：`_call_api` 见非 200 就降级到备用端点，
# 于是回答其实来自备用模型，而 `get_model_info()` 仍报主模型名 ——
# 这一轮排查时它让一次探针跑出了"llama-3.1-8b-instant 回话正常"的假结论。
# 核对办法（不消耗 token）：`python tools/probe_free_llm_matrix.py --list`
# 以及 `GET {base_url}/models`；`__init__` 里对"用了预设默认模型"会打 WARNING。
PRESET_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        # 2026-09-11 用 GET /models 核对过（当时共 14 个模型）
        "free_models": [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
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
        # 2026-09-11 用公开 /models 核对过（当时 22 个免费模型）
        "free_models": [
            "nvidia/nemotron-3-super-120b-a12b:free",
            "inclusionai/ling-3.0-flash-sante:free",
            "google/gemma-4-26b-a4b-it:free",
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

    @staticmethod
    def _compose_system_prompt(system_prompt: str = None,
                               reply_style: str = "concise",
                               system_prompt_concise: str = None,
                               system_prompt_detailed: str = None) -> str:
        """决定最终发给模型的 system message。

        ## 为什么不能"按 reply_style 二选一"

        旧实现是：
            if reply_style == "detailed" and system_prompt_detailed: 用 detailed
            elif reply_style == "concise" and system_prompt_concise: 用 concise
            else:                                                    用 system_prompt

        而**设置界面编辑的正是 `system_prompt`**。于是只要配置里
        `reply_style` 有值（本机出厂就是 `concise`），用户在设置里写的
        「人设提示词」就被**整段绕过去**，一个字都到不了模型 ——
        用户感知正是"人设设定了但没真正加载进角色"。
        实测：设置里写了 1935 字，模型实际收到的只有内置的 388 字。

        ## 现在的语义（人设与详略分开，各管各的）

        - **`system_prompt` = 人设本身**，无论详略都必须生效（用户可见的设置）；
        - **`system_prompt_concise` / `system_prompt_detailed` = 详略指令**，
          作为**附加段**拼在人设之后，而不是把人设替换掉；
        - 用户没写人设时（空/None），才退回内置默认人设，
          并保留详略段 —— 这样"没设置过"和"设置过"都合理。
        """
        user_persona = (system_prompt or "").strip()
        style_block = ""
        if reply_style == "detailed":
            style_block = (system_prompt_detailed or "").strip()
        elif reply_style == "concise":
            style_block = (system_prompt_concise or "").strip()

        if not user_persona:
            # 用户没写人设：详略段本身就是完整 prompt（保持出厂行为不变）
            return style_block or "你是欣雅，一个友善的AI桌面管家。"

        if not style_block:
            return user_persona

        # 用户写了人设 + 有详略段：人设在前（身份），详略在后（本次输出要求）
        # 用分隔线让模型能分清"我是谁"与"这次该怎么答"
        if style_block == user_persona:
            return user_persona          # 两份完全相同就别拼了
        return f"{user_persona}\n\n---\n\n{style_block}"

    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        base_url: str = None,
        provider: str = None,
        system_prompt: str = None,
        reply_style: str = "concise",
        system_prompt_concise: str = None,
        system_prompt_detailed: str = None,
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
        self._model_from_preset = False
        if provider and provider in PRESET_PROVIDERS:
            preset = PRESET_PROVIDERS[provider]
            self.base_url = base_url or preset["base_url"]
            env_key = preset["env_key"]
            self.api_key = api_key or os.environ.get(env_key, "")
            if not model and preset["free_models"]:
                model = preset["free_models"][0]
                self._model_from_preset = True
        else:
            self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

        self.model = model or "gpt-3.5-turbo"
        self.system_prompt = self._compose_system_prompt(
            system_prompt=system_prompt,
            reply_style=reply_style,
            system_prompt_concise=system_prompt_concise,
            system_prompt_detailed=system_prompt_detailed,
        )
        self.reply_style = reply_style
        #: 流式总时长硬上限（秒）。超过则用已收到的文本收尾。
        #: 依据：用户要求"回复 2 秒内"，而实测存在 30 秒的超时样本；
        #: 半句回复远好过让用户干等半分钟才听到"抱歉超时了"。
        self._stream_deadline_sec = 6.0
        logger.info(f"LLM 回复风格: {reply_style}")
        # 备用提供商（429 限流自动降级）
        self.fallback_api_key = fallback_api_key or ""
        self.fallback_base_url = (fallback_base_url or "").rstrip("/")
        self.fallback_model = fallback_model or ""
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._available = False

        # ── 外部依赖状态（P4-B3）──
        #
        # 为什么必须记下来：免费档配额打满时，HTTP 429 在这里只留下一行 error 日志，
        # 对上层表现为"模型没给出结果" —— 于是**外部配额问题伪装成了"我们的代码坏了"**。
        # 验收脚本与用户都看不出真相（缺陷 18 就是这么来的）。
        # 现在把"最近一次是不是被限流"变成可查状态：`quota_stats()`。
        self._quota_hits = 0          # 累计 429 次数
        self._last_quota_at = 0.0     # 最近一次 429 的时间戳
        self._last_error = ""         # 最近一次非 200 的简述（不含密钥）
        self._ok_calls = 0            # 累计成功次数
        #: "整条链路"的诊断串：主+备用**各自**的失败原因。
        #:
        #: 为什么不复用 `_last_error`：那个字段是**对外契约**，
        #: `quota_blocked` 靠它 `startswith("429")` 判定。把"两家都失败"的
        #: 复合描述写进去会让配额状态误报（实测改了立刻 4 条用例变红）。
        #: 所以"某一次请求的失败原因"与"整条链路为什么没能回答"分成两个字段。
        self._last_chain_error = ""

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
        # ⚠️ 没有显式给 model 时，插件会拿内置预设清单的**第一个**当默认值。
        # 那个清单是快照、会下架，而"下架的模型"不会报参数错 ——
        # 它会先失败、再被 `_call_api` 静默降级到备用端点，于是
        # **回答来自备用模型而 get_model_info() 报的是主模型名**。
        # 实测代价：一次探针据此得出"llama-3.1-8b-instant 回话正常"的假结论，
        # 而该模型在 Groq 早已 404（P4-B4）。所以这里把"我用的是猜来的默认值"说出来。
        if self._model_from_preset:
            logger.warning(
                "未显式指定 model，改用内置预设默认模型 %s —— 内置清单是快照，可能已下架；"
                "下架时会先失败再降级到备用端点（表现为更慢，且真正回答的模型与"
                "get_model_info() 报的不一致）。请用 config.yaml 显式配置 model，"
                "并用 tools/probe_free_llm_matrix.py --list 核对清单。",
                self.model,
            )
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

    def chat_stream(self, prompt: str, context=None):
        """流式对话（SSE），按中文标点切句返回列表。
        与 openai_api 插件同构，用于 LLM→TTS 管线首句即播。
        """
        if not self._available:
            return None
        try:
            import json as _json
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            if context:
                for msg in context:
                    messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": prompt})

            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "stream": True,
            }
            import requests as _req
            # timeout 用 (连接, 读取) 元组：connect 短（快速失败），read 覆盖
            # "两个 SSE 分片之间的最大间隔"。单值 30 会让整个流最多挂 30s，
            # 用户干等半分钟完全不知道发生了什么 —— 实测有 30s 的超时样本。
            r = _req.post(url, headers=headers, json=payload, stream=True,
                          timeout=(5, 12))
            if r.status_code != 200:
                logger.error(f"LLM 流式请求失败: {r.status_code} {r.text[:200]}")
                # 429 → 等一小会儿重试一次（TPM 窗口是滑动的，几百毫秒后常能过），
                # 仍失败再走备用提供商。
                if r.status_code == 429:
                    import time as _t
                    _t.sleep(1.2)
                    r2 = _req.post(url, headers=headers, json=payload, stream=True,
                                   timeout=(5, 12))
                    if r2.status_code == 200:
                        logger.info("[llm] 429 重试成功")
                        r = r2
                    else:
                        logger.warning("[llm] 429 重试仍失败，转备用提供商")
                        return self._fallback_stream(messages)
                else:
                    return self._fallback_stream(messages)
            full = []
            reasoning_chars = 0
            _deadline = time.monotonic() + self._stream_deadline_sec
            # ⚠️ 必须强制 UTF-8 解码。
            #   `iter_lines(decode_unicode=True)` 用的是 `r.encoding`，而流式响应
            #   的 Content-Type 通常不带 charset → requests 退回 **ISO-8859-1**。
            #   于是中文 UTF-8 字节被按 Latin-1 解成 "æå¤©æ¯ææä¸" 这种乱码：
            #   乱码文本既进了 TTS（用户听到的是天书），也进了对话历史
            #   （下一轮模型看到的是乱码上下文）—— 这正是"答非所问"的直接成因。
            r.encoding = "utf-8"
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.strip():
                    continue
                # SSE 事件名行（"event: error" 等）不是 JSON，直接跳过 ——
                # 原实现没跳过它，每轮都抛一次 JSONDecodeError 走异常分支。
                if line.startswith("event:"):
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if line == "[DONE]":
                    break
                try:
                    obj = _json.loads(line)
                except (_json.JSONDecodeError, ValueError):
                    continue
                choices = obj.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta", {}) or {}
                # ⚠️ 只收 content，**不收 reasoning**。
                #   部分模型（gpt-oss / compound 系列）是推理模型，会先把思维链
                #   放在 `delta.reasoning` 里吐出来；若把两者混在一起，TTS 会念出
                #   "用户问我明天星期几，我得先推算一下日期……"这种旁白 —— 用户听到的
                #   就是驴唇不对马嘴。但 reasoning 要计数：它是纯开销，
                #   而且实测会**吃光 max_tokens 让 content 全空**（gpt-oss-20b
                #   1078 字符思维链后正文为空），必须能识别出来而不是静默返回空。
                if delta.get("reasoning"):
                    reasoning_chars += len(delta["reasoning"])
                content = delta.get("content")
                if content:
                    full.append(content)
                # 总时长硬上限：即使每个分片都在 read timeout 之内到达，
                # 一整轮也可能拖很久（推理模型尤其）。超了就用手上已有的文本收尾，
                # 而不是让用户继续干等 —— 半句回复远好过 30 秒沉默。
                if time.monotonic() > _deadline:
                    logger.warning(
                        "[llm] 流式超过总时长上限 %.1fs，提前收尾（已收 %d 字）",
                        self._stream_deadline_sec, sum(len(x) for x in full),
                    )
                    break
            text = "".join(full).strip()
            if not text:
                if reasoning_chars:
                    logger.warning(
                        "[llm] 流式结束但正文为空（推理内容 %d 字符吃光了 max_tokens=%s）"
                        "→ 转备用链路", reasoning_chars, self.max_tokens,
                    )
                    return self._fallback_stream(messages)
                logger.warning("[llm] 流式返回空文本")
                return None
            from core.text_utils import split_sentences
            return split_sentences(text)
        except Exception as e:
            logger.error(f"LLM 流式请求异常: {e}")
            return None

    def _fallback_stream(self, messages):
        """流式失败时降级到非流式"""
        try:
            result = self._call_api(messages)
            if not result:
                # 关键：不能静默返回 None。
                # 原先这里什么都不记，于是"流式失败 → 非流式也失败"这条路上
                # 用户看到的是**完全没有任何反馈**，而真正原因（两家都挂了、
                # 或者备用模型名已下架）只存在于 `_call_api` 内部的日志里。
                # 这里补一条，把 `_last_error` 带出来，让上层能说出原因。
                logger.error(
                    "流式与非流式都失败了，本次没有回复（链路原因：%s）",
                    self._last_chain_error or self._last_error or "未知",
                )
                return None
            from core.text_utils import split_sentences
            return split_sentences(result)
        except Exception as e:
            self._last_chain_error = f"降级失败: {type(e).__name__}"
            logger.error("降级到非流式时异常: %s", e)
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

        # 主提供商失败（可能是 429/超时）→ 尝试备用。
        #
        # ⚠️ 这里**绝不能改写 `_last_error`**：它是对外契约字段，
        #    `quota_stats()["quota_blocked"]` 靠 `_last_error.startswith("429")` 判定，
        #    把它包成"主(...)+备用(...)"会让配额状态**误报为未限流**
        #    （实测：改了这里立刻让 4 条既有用例变红）。
        #    所以诊断信息另开一个字段 `_last_chain_error`，两个字段各管各的。
        main_err = self._last_error

        if not (self.fallback_api_key and self.fallback_base_url and self.fallback_model):
            self._last_chain_error = f"主({main_err or '未知'})；且未配置备用提供商"
            logger.error(
                "主提供商失败（%s），且**没有配置备用提供商** —— "
                "这条链路上没有任何兜底，用户会直接得不到回复。"
                "请检查 plugins.llm.params 的 fallback_api_key / "
                "fallback_base_url / fallback_model 三项是否齐全。",
                main_err or "原因未知",
            )
            return None

        logger.warning(f"主提供商失败，降级到备用模型: {self.fallback_model}")
        result = self._request_once(
            messages, self.fallback_base_url, self.fallback_api_key, self.fallback_model
        )
        if result is not None:
            return result

        # 两家都失败：把两个原因**一起**记进 `_last_chain_error`（不动 `_last_error`）。
        fallback_err = self._last_error
        self._last_chain_error = (
            f"主({main_err or '未知'}) + 备用({fallback_err or '未知'}) 均失败"
        )
        logger.error(
            "主提供商与备用提供商**都失败了**，本次没有回复。"
            "主失败原因=%s；备用失败原因=%s。"
            "若备用是 404，通常表示该 `:free` 模型已被上游下架，"
            "需要换一个仍在架的免费模型（见 config.yaml 的 fallback_model）。"
            "若备用是'HTTP 200 但空回复'，多为 reasoning 型模型把正文放进了思维链字段。",
            main_err or "未知", fallback_err or "未知",
        )
        return None

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
                # 429 单独记账：它是**外部配额**，不是"模型不行"（缺陷 18）
                if response.status_code == 429:
                    self._quota_hits += 1
                    self._last_quota_at = time.time()
                    self._last_error = "429 配额/限流"
                else:
                    self._last_error = f"HTTP {response.status_code}"
                logger.error(
                    f"API 错误: {response.status_code} - {response.text[:300]}"
                )
                return None

            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0].get("message", {}).get("content", "")
                if content:
                    logger.info(f"回复成功，长度: {len(content)}")
                    self._ok_calls += 1
                    self._last_error = ""      # 恢复正常 → 解除"被限流"状态
                    return content.strip()

            # HTTP 200 但没有可用内容 —— 这也是一种**失败**，必须记账。
            # 原先这里只打一行 warning 就 `return None`，**不写 `_last_error`**，
            # 于是上层拼"两家分别怎么了"时备用那半只能显示"未知"，
            # 排查者会误以为"备用没被调用过"（实测踩到：主 429 + 备用未知）。
            # 常见成因：`reasoning` 型模型把内容全放进了思维链字段，
            # 正文为空；或上游返回了空 choices。
            self._last_error = "HTTP 200 但空回复"
            logger.warning("API 返回空回复")
            return None

        except Exception as e:
            # 也记进"最近一次失败原因"：连不上、超时同样是**外部依赖状态**，
            # 只写类型名不写消息（消息里可能带 URL，避免任何形式的意外泄露）。
            self._last_error = f"调用异常: {type(e).__name__}"
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
            tools: 工具 schema 列表。**扁平与"已包好"两种形制都收**
                （D16：`LLMRouter.TOOL_SCHEMAS` 是扁平的，
                `ToolRegistry.to_llm_schemas()` 是已包好的）；畸形条目由
                `to_openai_tools` 显式拒绝，不再静默产生匿名工具
            timeout: 覆盖默认超时（秒）；调用方（LLMRouter）不传，此处给 12s
                     兜住"提示词里带 21 个工具 schema"时更长的往返

        Returns:
            {"name": 工具名, "arguments": {...}}；未选工具或失败时返回 None

        Raises:
            ToolSchemaError: `tools` 形制不合法（**显式报错，不静默降级**）
        """
        if not self._available:
            logger.error("LLM 不可用")
            return None
        if not tools:
            return None

        # 形制归一放在**发请求之前**：匿名工具（没有 function.name）发出去只会
        # 换来 400 或空 tool_calls，而上层看到的是"模型没选工具" —— 与 D14 同类的
        # 静默失效。这里直接抛 ToolSchemaError，让接错在调用点就现形。
        payload_tools = to_openai_tools(tools)
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

        except ToolSchemaError:
            # 形制错误**必须穿透**这个宽口 `except Exception`（D16 修复的实质）：
            # 被吃掉就退化成 `(False, None)` → 重试备用端点 → 照样失败 →
            # 上层只看到 `None`，与"模型本轮没选工具"无法区分。
            raise
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

    def quota_stats(self) -> Dict[str, Any]:
        """外部依赖状态（P4-B3）：配额/限流是否正在挡路

        `quota_blocked` 的语义是**"当前是否处于被限流状态"**：
        最近一次请求以 429 结束即为 True，任何一次成功调用都会把它复位。
        这样它回答的是"现在能不能用"，而不是"历史上错过多少次"（后者看 `quota_hits`）。

        Returns:
            `{quota_blocked, quota_hits, last_quota_at, last_error, ok_calls,
              fallback_configured, last_chain_error}`
            —— 不含任何密钥。
        """
        return {
            "quota_blocked": self._last_error.startswith("429"),
            "quota_hits": self._quota_hits,
            "last_quota_at": self._last_quota_at,
            "last_error": self._last_error,
            "ok_calls": self._ok_calls,
            "fallback_configured": bool(self.fallback_api_key and self.fallback_model),
            # 整条链路为什么没能回答（主 + 备用各自的原因）。
            # 与 `last_error` 分开：那个是"某一次请求"的，这个是"这一轮问答"的。
            "last_chain_error": self._last_chain_error,
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
