"""LLM 路由（Service Provider）

规则路由覆盖不到的表达（"那个上次说的东西"、"帮我处理一下"）交给 LLM
做 function calling 判定。

关键设计：
- LLM 调用以**函数注入**方式提供，签名 `(system, user, tools) -> Optional[dict]`，
  返回 `{"name": "file_search", "arguments": {...}}`；不注入则视为不可用
- 意图词表从 RuleRouter.describe_intents() 复用，避免两处维护
- 任何失败（超时/格式错/未知工具）都抛或返回低置信度 chat，由 HybridRouter 兜底
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from agent.message import AgentCommand
from agent.seams.router import RouterService

logger = logging.getLogger(__name__)

#: LLM 调用签名：(system_prompt, user_text, tools) -> Optional[dict]
LLMFunctionCall = Callable[[str, str, List[Dict[str, Any]]], Optional[Dict[str, Any]]]

#: 工具清单（供 LLM 选择）——与 RuleRouter 的意图表保持一致
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "file_search",
        "description": "在常用目录（桌面/文档/下载/图片）搜索文件",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "文件名模式，如 *合同* 或 *.pdf"},
                "dirs": {"type": "array", "items": {"type": "string"},
                         "description": "限制目录，取值 Desktop/Documents/Downloads/Pictures"},
                "time_range": {"type": "string",
                               "enum": ["today", "yesterday", "last_2_days",
                                        "last_week", "this_week"]},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "file_list",
        "description": "列出某个常用目录下的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "dirs": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "file_read",
        "description": "读取文件内容，或用默认程序打开",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "文件名或路径"}},
            "required": ["target"],
        },
    },
    {
        "name": "file_rename",
        "description": "重命名文件",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "file_move",
        "description": "把文件移动到另一个常用目录",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "dest": {"type": "string",
                         "description": "Desktop/Documents/Downloads/Pictures"},
            },
            "required": ["source", "dest"],
        },
    },
    {
        "name": "file_delete",
        "description": "把文件移到回收站（可恢复）",
        "parameters": {
            "type": "object",
            "properties": {
                "targets": {"type": "array", "items": {"type": "string"}},
                "pattern": {"type": "string"},
                "dirs": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "chat",
        "description": "与文件操作无关的闲聊或常识问答",
        "parameters": {"type": "object", "properties": {}},
    },
    # ── 系统工具 ──
    {
        "name": "system_info",
        "description": "查询电脑状态：CPU、内存、电量、磁盘占用",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string",
                           "enum": ["all", "cpu", "memory", "battery", "disk"]},
            },
        },
    },
    {
        "name": "clipboard",
        "description": "读取或写入剪贴板文本",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set"]},
                "text": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "open_app",
        "description": "用默认程序打开文件、文件夹或已安装应用（记事本/计算器/画图等）",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "screenshot",
        "description": "截取当前屏幕并保存为图片",
        "parameters": {
            "type": "object",
            "properties": {"filename": {"type": "string"}},
        },
    },
    {
        "name": "run_command",
        "description": "执行一条系统命令（高风险，需用户确认）",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    # ── 生产力工具 ──
    {
        "name": "calculate",
        "description": "计算数学表达式",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "translate",
        "description": "把文本翻译成指定语言",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "target_lang": {"type": "string",
                                "enum": ["zh", "en", "ja", "ko", "fr", "de", "es", "ru"]},
            },
            "required": ["text"],
        },
    },
    {
        "name": "reminder",
        "description": "在指定时间后提醒用户做某事",
        "parameters": {
            "type": "object",
            "properties": {
                "what": {"type": "string"},
                "minutes": {"type": "number"},
            },
            "required": ["what"],
        },
    },
    {
        "name": "weather",
        "description": "查询某个城市的天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
        },
    },
    # ── 浏览器工具 ──
    {
        "name": "web_open",
        "description": "用默认浏览器打开网址或常见网站",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "在搜索引擎中查询关键词",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "engine": {"type": "string", "enum": ["bing", "baidu", "google"]},
                "open_browser": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_read",
        "description": "读取一个网页的正文内容",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]

#: 允许 LLM 选择的动作（由 TOOL_SCHEMAS 推导）
ALLOWED_ACTIONS = {t["name"] for t in TOOL_SCHEMAS}


class LLMRouter(RouterService):
    """用 LLM function calling 做意图路由

    用法：
        router = LLMRouter(llm_call=ai_service.route_with_tools)
        cmd = router.route("那个上次说的文件再打开看看", {})
    """

    capability_name = "router"
    provider_name = "llm"

    def __init__(
        self,
        llm_call: Optional[LLMFunctionCall] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 5.0,
        system_prompt: str = "",
    ) -> None:
        """
        Args:
            llm_call: LLM function calling 调用函数；None 时本 Provider 不可用
            tools: 工具 schema 列表（默认 TOOL_SCHEMAS）
            timeout: 调用超时（秒）——由注入函数自行遵守，这里仅记录
            system_prompt: 系统提示（附加在默认提示之后）
        """
        self._llm = llm_call
        self._tools = tools if tools is not None else TOOL_SCHEMAS
        self._timeout = timeout
        self._system_prompt = system_prompt or ""
        self._calls = 0
        self._failures = 0

    @property
    def available(self) -> bool:
        """是否可实际调用（未注入 LLM 时不可用）"""
        return self._llm is not None

    # ── 主入口 ──

    def route(self, text: str, context: Dict[str, Any] = None) -> AgentCommand:
        context = context or {}

        if not text or not str(text).strip():
            return self._chat(text, 0.0)

        if self._llm is None:
            logger.debug("[llm_router] 未注入 LLM 调用，视为不可用")
            return self._chat(text, 0.0)

        try:
            self._calls += 1
            raw = self._llm(self._build_system(), str(text), self._tools)
        except Exception as e:
            self._failures += 1
            logger.warning("[llm_router] 调用失败: %s", e)
            return self._chat(text, 0.0)

        return self._parse(text, raw, context)

    def _parse(
        self,
        text: str,
        raw: Optional[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> AgentCommand:
        """解析 LLM 返回的 function call"""
        if not raw:
            self._failures += 1
            logger.debug("[llm_router] LLM 返回空")
            return self._chat(text, 0.0)

        # 兼容两种常见返回形态
        name, args = self._extract_call(raw)
        if not name:
            self._failures += 1
            logger.debug("[llm_router] 无法解析 function call: %r", raw)
            return self._chat(text, 0.0)

        if name not in self.allowed_actions():
            self._failures += 1
            logger.warning("[llm_router] LLM 选择了未知动作: %s", name)
            return self._chat(text, 0.0)

        # LLM 判定为闲聊
        if name == "chat":
            return self._chat(text, 0.75)

        params = args if isinstance(args, dict) else {}
        # 剔除空值，避免污染工具参数校验
        params = {k: v for k, v in params.items() if v not in (None, "", [], {})}

        # 对 file_delete 补一个 permanent=False（硬约束在工具侧仍会拒绝 true）
        if name == "file_delete":
            params.pop("permanent", None)

        cmd = AgentCommand(
            action=name,
            params=params,
            raw_text=text,
            confidence=0.8,          # LLM 判定成功给固定置信度
            source=context.get("source", "llm"),
        )
        logger.info("[llm_router] %r → %s", text[:30], cmd)
        return cmd

    @staticmethod
    def _extract_call(raw: Dict[str, Any]):
        """从多种返回形态中提取 (name, arguments)

        支持：
        - {"name": ..., "arguments": {...}}
        - {"function": {"name": ..., "arguments": "{...}"}}
        - {"tool_calls": [{"function": {...}}]}
        """
        if not isinstance(raw, dict):
            return None, {}

        # 形态 3
        calls = raw.get("tool_calls")
        if isinstance(calls, list) and calls:
            first = calls[0]
            if isinstance(first, dict):
                raw = first.get("function") or first

        # 形态 2
        fn = raw.get("function")
        if isinstance(fn, dict):
            raw = fn

        name = raw.get("name") or raw.get("tool")
        args = raw.get("arguments", raw.get("args", {}))

        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                args = {}

        return (str(name) if name else None), (args or {})

    def _build_system(self) -> str:
        """构造系统提示"""
        lines = [
            "你是桌面助手的意图识别模块。根据用户这句话，从给定工具中选一个并填好参数。",
            "规则：",
            "1. 只输出一次 function call，不要解释",
            "2. 与文件操作无关的闲聊、常识问答、天气、写诗等一律选 chat",
            "3. 文件名模式用 glob（如 *合同*），目录只能用 Desktop/Documents/Downloads/Pictures",
            "4. 用户说'第一个/那个/那些'时，保留原词作为参数值，不要自己猜测",
        ]
        if self._system_prompt:
            lines.append(self._system_prompt)
        return "\n".join(lines)

    def _chat(self, text: str, confidence: float) -> AgentCommand:
        return AgentCommand(
            action="chat",
            params={},
            raw_text=text or "",
            confidence=confidence,
            source="llm",
        )

    # ── 接口实现 ──

    def allowed_actions(self) -> set:
        """本次实例允许的动作集合（基于实例持有的 tools，支持自定义 schema）"""
        return {t["name"] for t in self._tools}

    def supported_actions(self) -> List[str]:
        return sorted(self.allowed_actions())

    def describe_intents(self) -> List[Dict[str, Any]]:
        return [
            {"action": t["name"], "description": t["description"],
             "category": "llm", "keywords": []}
            for t in self._tools
        ]

    def stats(self) -> Dict[str, int]:
        """调用统计"""
        return {"calls": self._calls, "failures": self._failures}

    def __repr__(self) -> str:
        return (
            f"<LLMRouter available={self.available} "
            f"calls={self._calls} failures={self._failures}>"
        )
