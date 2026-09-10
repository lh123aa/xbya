"""模板摘要器（Service Provider）

最快路径：纯字符串拼装，<1ms，零成本。
覆盖所有工具的结果形态；措辞固定但自然。
"""

import logging
from typing import Any, Dict, List

from agent.seams.summarizer import (
    SummarizerService,
    format_mtime,
    format_size,
    join_names,
    result_items,
)
from agent.tools.base import ToolResult

logger = logging.getLogger(__name__)


class TemplateSummarizer(SummarizerService):
    """模板摘要：按 (action, 结果形态) 选择模板

    模板选择优先级：
    1. 失败 → 直接用工具给的用户文案
    2. 空结果 → 直接用工具给的提示
    3. 有结构化数据 → 按 action 选模板
    4. 兜底 → 原样返回 result.summary
    """

    capability_name = "summarizer"
    provider_name = "template"

    def summarize(self, action: str, result: ToolResult) -> str:
        try:
            return self._summarize(action, result)
        except Exception as e:
            logger.warning("[summarizer] 模板摘要失败 (%s): %s", action, e)
            return result.summary or ""

    def _summarize(self, action: str, result: ToolResult) -> str:
        # 1. 失败 → 用工具的用户文案
        if not result.success:
            return result.summary or result.error or "操作没成功呢"

        items = result_items(result)

        # 2. 空结果 → 用工具的提示
        if not items:
            return result.summary or "没有找到内容呢"

        # 3. 按 action 分派
        builder = {
            "file_search": self._search,
            "file_list": self._list,
            "file_read": self._read,
            "file_rename": self._rename,
            "file_move": self._move,
            "file_delete": self._delete,
            "system_info": self._system,
            "clipboard": self._clipboard,
            "translate": self._translate,
            "calculate": self._calculate,
            "web_open": self._web_open,
            "web_search": self._web_search,
            "web_read": self._web_read,
            "reminder": self._reminder,
            "screenshot": self._generic,
            "open_app": self._generic,
        }.get(action)

        if builder is None:
            return result.summary or ""

        text = builder(items, result)
        return text or result.summary or ""

    # ── 文件类 ──

    def _search(self, items: List[Any], result: ToolResult) -> str:
        tail = "，只显示了前面这些" if result.truncated else ""

        if len(items) == 1:
            it = items[0] if isinstance(items[0], dict) else {}
            size = format_size(it.get("size"))
            when = format_mtime(it.get("mtime_text"))
            extra = "，".join(x for x in (size, when) if x)
            name = it.get("name", "那个文件")
            return f"找到「{name}」{('，' + extra) if extra else ''}{tail}"

        names = join_names(items)
        suffix = f"（{names}）" if names else ""
        return f"找到 {len(items)} 个文件{suffix}{tail}"

    def _list(self, items: List[Any], result: ToolResult) -> str:
        dirs = [i.get("name") for i in items if isinstance(i, dict)]
        summary = f"一共 {len(items)} 项"
        if dirs:
            summary += f"：{join_names(items)}"
        if result.truncated:
            summary += "，只显示了前一部分"
        return summary

    def _read(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        content = data.get("content")
        if content:
            preview = str(content).strip().replace("\n", " ")[:60]
            return f"读到了：{preview}"
        name = data.get("name") or (items[0].get("name") if isinstance(items[0], dict) else "")
        return f"已经打开「{name}」啦" if name else (result.summary or "")

    def _rename(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        old, new = data.get("old"), data.get("new")
        if old and new:
            return f"「{old}」已经改名叫「{new}」啦"
        return result.summary or ""

    def _move(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        name = data.get("name")
        dest = str(data.get("to") or "")
        folder = dest.replace("\\", "/").rsplit("/", 1)[0].rsplit("/", 1)[-1] if dest else ""
        if name and folder:
            return f"「{name}」已经移到 {folder} 啦"
        return result.summary or ""

    def _delete(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        deleted = data.get("deleted") if isinstance(data.get("deleted"), list) else items
        count = len(deleted)
        failed = data.get("failed") if isinstance(data.get("failed"), list) else []
        missing = data.get("missing") or 0

        text = f"已经把 {count} 个文件移到回收站啦"
        if missing:
            text += f"（有 {missing} 个没找到）"
        if failed:
            text += f"，{len(failed)} 个没删成"
        return text

    # ── 其他类 ──

    def _system(self, items: List[Any], result: ToolResult) -> str:
        data = items[0] if items and isinstance(items[0], dict) else {}
        parts = []
        mapping = [
            ("cpu_percent", "CPU 用了 {v}%"),
            ("memory_percent", "内存用了 {v}%"),
            ("battery_percent", "电量 {v}%"),
            ("disk_percent", "磁盘用了 {v}%"),
        ]
        for key, tpl in mapping:
            v = data.get(key)
            if v is not None:
                parts.append(tpl.format(v=v))
        if parts:
            return "，".join(parts)
        return result.summary or ""

    def _clipboard(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        text = data.get("text")
        if text:
            preview = str(text).strip().replace("\n", " ")[:40]
            return f"剪贴板里是：{preview}"
        return result.summary or "已复制好啦"

    def _translate(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        text = data.get("translated") or data.get("text")
        if text:
            return str(text).strip()[:100]
        return result.summary or ""

    def _calculate(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        expr, value = data.get("expression"), data.get("result")
        if expr is not None and value is not None:
            return f"{expr} 等于 {value}"
        return result.summary or ""

    def _web_open(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        title = data.get("title") or data.get("url")
        return f"已经打开「{title}」啦" if title else (result.summary or "已经打开啦")

    def _web_search(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        query = data.get("query")
        results = data.get("results") if isinstance(data.get("results"), list) else items
        count = len(results)
        head = f"「{query}」" if query else "这个"
        if count == 0:
            return f"{head}没搜到结果呢"
        titles = join_names(results, limit=2)
        return f"{head}搜到 {count} 条：{titles}" if titles else f"{head}搜到 {count} 条结果"

    def _web_read(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        title = data.get("title")
        text = data.get("text") or ""
        preview = str(text).strip().replace("\n", " ")[:60]
        if title and preview:
            return f"「{title}」里面说：{preview}"
        return result.summary or ""

    def _reminder(self, items: List[Any], result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        when = data.get("when_text") or data.get("when")
        what = data.get("what") or "这件事"
        if when:
            return f"好的，{when}我会提醒你{what}"
        return result.summary or ""

    def _generic(self, items: List[Any], result: ToolResult) -> str:
        return result.summary or ""
