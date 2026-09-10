"""摘要能力接口定义（Service Definition）

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（SummarizerService）
- Provider:   agent/providers/summarizer/{template_sum,llm_sum}.py
- Consumer:   agent/pipeline.py

职责：把工具返回的结构化数据转成"人话"，供 TTS 播报与气泡展示。

两条实现路线的取舍：
- 模板：<1ms、零成本、措辞固定
- LLM：1~3s、需 API、措辞自然且带人设
故默认用 HybridSummarizer：简单结果走模板，复杂结果走 LLM。
"""

from abc import abstractmethod
from typing import Any, Dict

from agent.tools.base import ToolResult
from core.kernel.service import Service


class SummarizerService(Service):
    """摘要能力接口

    实现者约定：
    - summarize() 不得抛异常；无法处理时原样返回 result.summary
    - 不得产生副作用（纯函数）
    - 返回文本应可直接用于 TTS 播报（无 Markdown、无 emoji 堆砌）
    """

    capability_name = "summarizer"

    @abstractmethod
    def summarize(self, action: str, result: ToolResult) -> str:
        """把工具结果转为自然语言摘要

        Args:
            action: 工具名（如 "file_search"）
            result: 工具执行结果

        Returns:
            人类可读摘要；失败时回退 result.summary
        """

    def should_refine(self, action: str, result: ToolResult) -> bool:
        """该结果是否值得"事后润色"（P2-2 异步摘要）

        Consumer（管线）先用 fast_summary() 立即播报，再据此判断是否值得
        额外花一次 LLM 调用产出更好的措辞。

        Returns:
            True=值得润色；默认 False（纯模板实现无需润色）
        """
        return False

    def fast_summary(self, action: str, result: ToolResult) -> str:
        """立即播报用的廉价摘要（P2-2 异步摘要）

        约定：不得调用网络/LLM，必须是"立刻能返回"的路径。
        Consumer 先用它播报，随后用 refine() 产出补播文案。
        默认实现等价于 summarize()。
        """
        return self.summarize(action, result)

    def refine(self, action: str, result: ToolResult) -> str:
        """产出润色版摘要（P2-2 异步摘要）

        约定：
        - 只返回"更好的措辞"；无法润色时返回空串（调用方据此不发补播事件）
        - 允许耗时（运行在独立线程）；不得抛异常
        - 不得修改 result

        Returns:
            润色文本；不可用时为空串
        """
        return ""


def format_size(num_bytes: Any) -> str:
    """字节数 → 口语化大小（"2.3兆"）"""
    try:
        size = float(num_bytes or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    if size < 1024:
        return f"{int(size)}字节"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}兆"
    return f"{size / 1024 / 1024 / 1024:.1f}G"


def format_mtime(mtime_text: Any) -> str:
    """时间文本 → 口语化（"1月15号"）"""
    text = str(mtime_text or "")
    if not text or text == "未知":
        return ""
    try:
        date_part, _, time_part = text.partition(" ")
        y, m, d = date_part.split("-")
        return f"{int(m)}月{int(d)}号"
    except Exception:
        return ""


def join_names(items: Any, limit: int = 3) -> str:
    """把对象列表的 name 拼成"a、b、c"，超出部分折叠"""
    if not isinstance(items, (list, tuple)):
        return ""
    names = []
    for it in items:
        if isinstance(it, dict):
            n = it.get("name")
        else:
            n = str(it)
        if n:
            names.append(str(n))
    if not names:
        return ""
    if len(names) <= limit:
        return "、".join(names)
    return "、".join(names[:limit]) + f" 等 {len(names)} 个"


def result_items(result: ToolResult) -> list:
    """从 ToolResult 中提取条目列表（兼容多种承载形式）"""
    data = result.data
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("deleted", "candidates", "files", "items"):
            v = data.get(key)
            if isinstance(v, list):
                return v
        return [data]
    return []


def params_of(result: ToolResult) -> Dict[str, Any]:
    """占位：供 Provider 覆写以携带原始参数（默认空）"""
    return {}
