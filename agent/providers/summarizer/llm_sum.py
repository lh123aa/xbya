"""LLM 润色摘要器（Service Provider）

用大模型把结构化结果说成"人话"，并带上欣雅的人设语气。

关键设计：
- LLM 调用以**函数注入**方式提供（`llm_call`），不直接依赖 plugins/llm，
  这样既解耦又可测（测试注入桩函数即可）
- 失败/超时/输出异常 一律回退到模板摘要（调用方给 `fallback`）
- 输出做清洗：去掉 Markdown/引号/前缀，限制长度，确保可直接 TTS 播报
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from agent.seams.summarizer import SummarizerService, result_items
from agent.tools.base import ToolResult

logger = logging.getLogger(__name__)

#: 摘要输出长度上限（字符）
MAX_SUMMARY_CHARS = 120

#: LLM 调用签名：(prompt) -> Optional[str]
LLMCall = Callable[[str], Optional[str]]

#: 各工具的中文名（用于提示词）
_ACTION_NAMES = {
    "file_search": "搜索文件",
    "file_list": "列出目录",
    "file_read": "读取文件",
    "file_rename": "重命名文件",
    "file_move": "移动文件",
    "file_delete": "删除文件",
    "system_info": "查询系统状态",
    "clipboard": "剪贴板操作",
    "translate": "翻译",
    "calculate": "计算",
    "web_open": "打开网页",
    "web_search": "网页搜索",
    "web_read": "读取网页",
    "reminder": "设置提醒",
}


class LLMSummarizer(SummarizerService):
    """用 LLM 把工具结果润色成自然语言

    用法：
        s = LLMSummarizer(llm_call=app.chat_once, persona="你是欣雅…",
                          fallback=TemplateSummarizer())
        text = s.summarize("file_search", result)
    """

    capability_name = "summarizer"
    provider_name = "llm"

    def __init__(
        self,
        llm_call: Optional[LLMCall] = None,
        persona: str = "",
        fallback: Optional[SummarizerService] = None,
        max_chars: int = MAX_SUMMARY_CHARS,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            llm_call: LLM 调用函数（接收提示词返回文本）；None 则始终走回退
            persona: 人设提示词（前若干字会拼进系统提示）
            fallback: 失败时的回退摘要器（通常是 TemplateSummarizer）
            max_chars: 输出长度上限
            enabled: 总开关（False 时直接回退）
        """
        self._llm = llm_call
        self._persona = persona or ""
        self._fallback = fallback
        self._max_chars = max(20, max_chars)
        self._enabled = enabled
        self._calls = 0
        self._failures = 0

    # ── 主入口 ──

    def summarize(self, action: str, result: ToolResult) -> str:
        if not self._enabled or self._llm is None:
            return self._fallback_text(action, result)

        try:
            prompt = self.build_prompt(action, result)
            self._calls += 1
            raw = self._llm(prompt)
            cleaned = self.clean(raw)
            if cleaned:
                return cleaned
            self._failures += 1
            logger.debug("[summarizer] LLM 返回空，回退模板")
        except Exception as e:
            self._failures += 1
            logger.warning("[summarizer] LLM 润色失败: %s", e)

        return self._fallback_text(action, result)

    def _fallback_text(self, action: str, result: ToolResult) -> str:
        if self._fallback is not None:
            try:
                return self._fallback.summarize(action, result)
            except Exception as e:
                logger.warning("[summarizer] 回退摘要器失败: %s", e)
        return result.summary or ""

    # ── 提示词 ──

    def build_prompt(self, action: str, result: ToolResult) -> str:
        """构造润色提示词"""
        action_name = _ACTION_NAMES.get(action, action)
        payload = self._compact_payload(result)

        lines = [
            f"你是桌面助手的语音播报模块。刚刚执行了「{action_name}」，"
            f"请把下面的执行结果说成一句自然、亲切的中文口语。",
            "",
            "要求：",
            "1. 只输出这一句话，不要任何前缀、解释、Markdown 或引号",
            f"2. 不超过 {self._max_chars} 个字",
            "3. 失败时说清原因并给一句安慰，不要机械复述错误码",
            "4. 数字与文件名保持原样，不要编造",
        ]
        if self._persona:
            lines.append(f"5. 语气参考这个人设：{self._persona[:120]}")
        lines += ["", "执行结果：", payload]
        return "\n".join(lines)

    @staticmethod
    def _compact_payload(result: ToolResult) -> str:
        """把结果压缩成给 LLM 看的精简 JSON（避免超长）"""
        items = result_items(result)
        compact: Dict[str, Any] = {
            "成功": bool(result.success),
            "原始摘要": (result.summary or "")[:200],
            "条目数": len(items),
        }
        if result.error:
            compact["错误"] = str(result.error)[:150]
        if result.truncated:
            compact["已截断"] = True

        samples: List[Any] = []
        for it in items[:5]:
            if isinstance(it, dict):
                samples.append({
                    k: it.get(k)
                    for k in ("name", "size_text", "mtime_text", "is_dir")
                    if it.get(k) is not None
                })
            else:
                samples.append(str(it)[:80])
        if samples:
            compact["样例"] = samples

        if isinstance(result.data, dict):
            for key in ("expression", "result", "query", "title", "when_text", "what",
                        "cpu_percent", "memory_percent", "battery_percent", "disk_percent"):
                if key in result.data:
                    compact[key] = result.data[key]

        try:
            return json.dumps(compact, ensure_ascii=False, indent=None)
        except Exception:
            return str(compact)[:500]

    # ── 输出清洗 ──

    def clean(self, raw: Any) -> str:
        """清洗 LLM 输出，确保可直接播报"""
        if not raw:
            return ""
        text = str(raw).strip()

        # 去掉代码块围栏
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
        # 去掉常见前缀（"好的，"/"回答："）
        text = re.sub(r"^(好的|嗯|答复|回答|摘要|输出)[：:，,]\s*", "", text)
        # 去掉包裹引号
        text = text.strip("\"'“”‘’")
        # 压平换行与多余空白
        text = re.sub(r"\s+", " ", text).strip()
        # 去掉 Markdown 强调符
        text = text.replace("**", "").replace("__", "")

        if len(text) > self._max_chars:
            text = text[: self._max_chars].rstrip("，,、 ") + "…"
        return text

    # ── 状态 ──

    def stats(self) -> Dict[str, int]:
        """调用统计"""
        return {"calls": self._calls, "failures": self._failures}

    def set_enabled(self, enabled: bool) -> None:
        """开关（关闭后直接走模板）"""
        self._enabled = bool(enabled)

    def __repr__(self) -> str:
        return f"<LLMSummarizer enabled={self._enabled} calls={self._calls}>"
