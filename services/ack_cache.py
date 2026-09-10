"""确认语缓存池

目的：把「用户说完 → 听到第一声反馈」的延迟从 ~1.2s 压到 ~0.05s。

两层缓存：
1. **分类缓存（预热）** — 高频确认语（"好的，我看看~"）在启动时预合成，
   按 category 存放，命中时随机取一条，延迟 ≈0ms。
2. **文本 LRU（动态）** — 长尾短语（如含文件预览的确认问句
   "找到 12 张截图，共 24.3MB，要移到回收站吗？"）首次合成后进入有界 LRU，
   重复出现直接命中，不再实时调 TTS。

设计要点：
- 预热在后台线程进行，不阻塞启动
- 预热失败不影响主流程（降级为实时合成）
- 线程安全：缓存写入与 LRU 顺序升级加锁，读取无锁（dict 读取是原子的）
- LRU 有界：超容量淘汰最久未用，避免长跑进程内存无限增长
"""

import logging
import random
import sys
import threading
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 动态 LRU 默认容量（条）
DEFAULT_LRU_SIZE = 64


#: 确认语库：分类 → 候选短语
ACK_PHRASES: Dict[str, List[str]] = {
    "search": [
        "好的，我找找看~",
        "稍等，我搜一下~",
        "让我翻翻看~",
    ],
    "read": [
        "好的，我打开看看~",
        "这就去看~",
    ],
    "write": [
        "好的，我这就处理~",
        "马上帮你弄~",
    ],
    "delete": [
        "好，我先核对一下~",
        "让我看看要删哪些~",
    ],
    "system": [
        "好的，我看看~",
        "稍等哦~",
    ],
    "default": [
        "好的，我看看~",
        "稍等一下哦~",
        "让我处理一下~",
    ],
}


class AckCache:
    """确认语缓存池

    用法：
        cache = AckCache()
        cache.warm_up(tts_synthesize)     # 后台预热
        audio = cache.get("search")        # 命中返回 bytes，未命中返回 None
    """

    def __init__(
        self,
        phrases: Optional[Dict[str, List[str]]] = None,
        warmup_async: bool = True,
        lru_size: int = DEFAULT_LRU_SIZE,
    ) -> None:
        """
        Args:
            phrases: 自定义短语库；None 使用 ACK_PHRASES
            warmup_async: 预热是否在后台线程执行
            lru_size: 动态文本 LRU 容量；0 表示关闭动态缓存（每次都实时合成）
        """
        self._phrases = phrases if phrases is not None else ACK_PHRASES
        self._cache: Dict[str, List[bytes]] = {}   # category → [audio, ...]
        self._text_index: Dict[bytes, str] = {}    # audio → text（调试用）
        self._lock = threading.Lock()
        self._warmed = False
        self._warmup_thread: Optional[threading.Thread] = None
        self._synthesize: Optional[Callable[[str], Optional[bytes]]] = None
        self._warmup_async = warmup_async
        self._cancelled = False

        # 动态 LRU：text → audio（最近使用在末尾）
        self._lru_maxsize = max(0, int(lru_size))
        self._lru: "OrderedDict[str, bytes]" = OrderedDict()
        self._lru_stats: Dict[str, int] = {"hits": 0, "misses": 0, "evictions": 0}
        self._by_text: Dict[str, bytes] = {}       # text → audio（预热短语反查）

    # ══════════════════════════════════════════════
    #  预热
    # ══════════════════════════════════════════════

    def warm_up(self, synthesize: Callable[[str], Optional[bytes]]) -> None:
        """预热缓存

        Args:
            synthesize: TTS 合成函数，签名 (text) -> Optional[bytes]
        """
        self._synthesize = synthesize

        if self._warmup_async:
            self._warmup_thread = threading.Thread(
                target=self._do_warm_up,
                name="ack-warmup",
                daemon=True,
            )
            self._warmup_thread.start()
            logger.info("[ack] 确认语预热已在后台启动")
        else:
            self._do_warm_up()

    def _do_warm_up(self) -> None:
        """执行预热（可能运行在后台线程）

        多处提前退出，避免在解释器关闭或已取消时仍发起 TTS 请求
        （否则会报 "cannot schedule new futures after interpreter shutdown"）。
        """
        if self._synthesize is None:
            return

        ok = 0
        failed = 0

        for category, phrases in self._phrases.items():
            if self._cancelled or sys.is_finalizing():
                logger.debug("[ack] 预热提前结束（取消/解释器关闭中）")
                break

            audios: List[bytes] = []
            for text in phrases:
                if self._cancelled or sys.is_finalizing():
                    break
                try:
                    audio = self._synthesize(text)
                except Exception as e:
                    logger.warning("[ack] 预热失败 %r: %s", text, e)
                    audio = None

                if audio:
                    audios.append(audio)
                    self._text_index[audio] = text
                    with self._lock:
                        self._by_text[text] = audio
                    ok += 1
                else:
                    failed += 1

            if audios:
                with self._lock:
                    self._cache[category] = audios

        if not self._cancelled:
            self._warmed = True
        logger.info("[ack] 确认语预热结束：成功 %d 条，失败 %d 条", ok, failed)

    def cancel(self) -> None:
        """取消预热（应用关闭时调用，避免后台线程继续发起 TTS 请求）"""
        self._cancelled = True
        logger.debug("[ack] 已请求取消预热")

    def close(self, timeout: float = 2.0) -> None:
        """取消预热并等待线程退出

        `cancel()` 只置标志位；若预热线程正阻塞在 TTS 调用里，它会继续存活。
        全量测试会反复装配/释放 Agent 栈，残留的预热线程堆积到解释器关闭阶段
        参与 teardown 竞态（技术债 D7 的参与者之一）。这里做有界 join。

        Args:
            timeout: 最长等待秒数；线程本身是 daemon，超时不会阻塞退出
        """
        self.cancel()

        thread = self._warmup_thread
        if thread is None or thread is threading.current_thread():
            return
        if thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.debug("[ack] 预热线程未在 %.1fs 内退出（已 daemon，不阻塞退出）", timeout)

    # ══════════════════════════════════════════════
    #  查询
    # ══════════════════════════════════════════════

    def get(self, category: str = "default") -> Optional[bytes]:
        """取一条确认语音频

        Args:
            category: 分类（search/read/write/delete/system/default）

        Returns:
            音频字节；未预热或该分类无缓存时返回 None（调用方降级实时合成）
        """
        audios = self._cache.get(category)
        if not audios:
            audios = self._cache.get("default")
        if not audios:
            return None
        return random.choice(audios)

    def get_text(self, category: str = "default") -> str:
        """取一条确认语文本（用于气泡展示）"""
        phrases = self._phrases.get(category) or self._phrases.get("default") or ["好的"]
        return random.choice(phrases)

    def pick(self, category: str = "default") -> tuple:
        """同时取音频与文本（保证二者对应）

        Returns:
            (audio|None, text)
        """
        audios = self._cache.get(category) or self._cache.get("default")
        if not audios:
            return None, self.get_text(category)

        audio = random.choice(audios)
        text = self._text_index.get(audio) or self.get_text(category)
        return audio, text

    # ══════════════════════════════════════════════
    #  动态 LRU（长尾短语）
    # ══════════════════════════════════════════════

    @property
    def lru_maxsize(self) -> int:
        """动态 LRU 容量（0 表示关闭）"""
        return self._lru_maxsize

    def get_or_synthesize(
        self,
        text: str,
        synthesize: Optional[Callable[[str], Optional[bytes]]] = None,
    ) -> Optional[bytes]:
        """按文本取音频，未命中则合成并写入 LRU

        查找顺序：
            1. 动态 LRU 命中 → 提升优先级并返回
            2. 预热短语反查命中 → 提升进 LRU 并返回
            3. 都未命中 → 调用 synthesize 合成，成功后写入 LRU

        合成失败（返回 None/空/抛异常）不写入缓存，调用方需自行降级。

        Args:
            text: 待播报文本
            synthesize: 合成函数；None 时用 warm_up 传入的那个

        Returns:
            音频字节；无法合成时返回 None
        """
        key = (text or "").strip()
        if not key:
            return None

        if self._lru_maxsize > 0:
            with self._lock:
                cached = self._lru.get(key)
                if cached is not None:
                    self._lru.move_to_end(key)
                    self._lru_stats["hits"] += 1
                    return cached

                warmed = self._by_text.get(key)
                if warmed is not None:
                    self._lru[key] = warmed
                    self._lru.move_to_end(key)
                    self._lru_stats["hits"] += 1
                    self._evict_locked()
                    return warmed

                self._lru_stats["misses"] += 1
        else:
            # 动态缓存关闭：仍然记未命中，便于观测"关掉缓存后省了多少次合成"
            with self._lock:
                self._lru_stats["misses"] += 1

        synth = synthesize or self._synthesize
        if synth is None:
            return None

        try:
            audio = synth(key)
        except Exception as e:
            logger.warning("[ack] 动态合成失败 %r: %s", key, e)
            return None

        if not audio:
            return None

        if self._lru_maxsize > 0:
            with self._lock:
                self._lru[key] = audio
                self._lru.move_to_end(key)
                self._evict_locked()

        return audio

    def _evict_locked(self) -> None:
        """淘汰超容量条目（调用方必须已持锁）"""
        while len(self._lru) > self._lru_maxsize:
            self._lru.popitem(last=False)
            self._lru_stats["evictions"] += 1

    def lru_stats(self) -> Dict[str, int]:
        """LRU 统计快照"""
        with self._lock:
            snap = dict(self._lru_stats)
            snap["size"] = len(self._lru)
            snap["maxsize"] = self._lru_maxsize
            return snap

    def lru_size(self) -> int:
        """当前 LRU 条目数"""
        with self._lock:
            return len(self._lru)

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    @property
    def warmed(self) -> bool:
        """预热是否已完成"""
        return self._warmed

    def cached_count(self) -> int:
        """已缓存的音频条数"""
        return sum(len(v) for v in self._cache.values())

    def categories(self) -> List[str]:
        """已有缓存的分类"""
        return list(self._cache.keys())

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """等待预热完成（测试用）

        Returns:
            True=预热已完成；False=超时
        """
        if self._warmup_thread is None:
            return self._warmed
        self._warmup_thread.join(timeout=timeout)
        return self._warmed

    def clear(self) -> None:
        """清空缓存（含动态 LRU 与统计）"""
        with self._lock:
            self._cache.clear()
            self._text_index.clear()
            self._by_text.clear()
            self._lru.clear()
            for key in self._lru_stats:
                self._lru_stats[key] = 0
        self._warmed = False


def category_for_action(action: str) -> str:
    """工具名 → 确认语分类

    Args:
        action: 工具名（如 "file_delete"）

    Returns:
        分类名；未知工具返回 "default"
    """
    action = (action or "").lower()
    if "search" in action or "list" in action:
        return "search"
    if "read" in action or "open" in action:
        return "read"
    if "delete" in action or "remove" in action:
        return "delete"
    if "write" in action or "rename" in action or "move" in action or "copy" in action:
        return "write"
    if "system" in action or "info" in action or "clipboard" in action:
        return "system"
    return "default"
