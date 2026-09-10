"""通用对象注册表

DSH「注册即副作用」思想的实现：
- register() 返回 disposer，可逆
- names()/items() 返回快照，避免遍历时修改导致的问题

用途：
- ToolRegistry = Registry[BaseTool]
- ProviderRegistry = Registry[Service]
- 任意同类型对象的具名管理
"""

import logging
from typing import Callable, Dict, Generic, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Registry(Generic[T]):
    """通用对象注册表

    用法：
        reg = Registry[MyTool](kind="tool")
        dispose = reg.register(tool)          # 自动从 tool.name 推导注册名
        reg.register(other, name="custom")    # 显式指定注册名
        dispose()                             # 注销
    """

    def __init__(self, kind: str = "item") -> None:
        """
        Args:
            kind: 注册对象类型的中文描述，用于日志和报错信息
        """
        self._items: Dict[str, T] = {}
        self._kind = kind

    # ── 注册 / 注销 ──

    def register(self, item: T, name: Optional[str] = None) -> Callable[[], None]:
        """注册对象

        Args:
            item: 要注册的对象
            name: 注册名；为 None 时从 item.name 属性推导

        Returns:
            注销函数（幂等：仅当注册项未被替换时才移除）

        Raises:
            ValueError: 无法确定注册名
        """
        if name is None:
            name = getattr(item, "name", None)
        if not name:
            raise ValueError(
                f"无法确定注册名：{self._kind} 无 name 属性且未显式指定 name"
            )

        if name in self._items:
            logger.warning("[registry] 覆盖同名 %s: %s", self._kind, name)

        self._items[name] = item

        def dispose() -> None:
            # 仅当当前注册项仍是本对象时才移除，避免误删后来的覆盖者
            if self._items.get(name) is item:
                self._items.pop(name, None)

        return dispose

    def unregister(self, name: str) -> bool:
        """按名注销

        Returns:
            True=成功移除；False=不存在
        """
        if name in self._items:
            self._items.pop(name)
            return True
        return False

    # ── 查询 ──

    def get(self, name: str) -> Optional[T]:
        """按名获取，不存在返回 None"""
        return self._items.get(name)

    def has(self, name: str) -> bool:
        """是否存在指定注册名"""
        return name in self._items

    def names(self) -> List[str]:
        """所有注册名（快照）"""
        return list(self._items.keys())

    def items(self) -> List[T]:
        """所有对象（快照）"""
        return list(self._items.values())

    def filter(self, predicate: Callable[[T], bool]) -> List[T]:
        """按谓词过滤（快照）

        Args:
            predicate: 返回 True 表示保留

        Returns:
            符合条件的对象列表
        """
        return [v for v in self._items.values() if predicate(v)]

    def find_first(self, predicate: Callable[[T], bool]) -> Optional[T]:
        """返回第一个符合条件的对象"""
        for v in self._items.values():
            if predicate(v):
                return v
        return None

    # ── 容器协议 ──

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self):
        return iter(list(self._items.values()))

    def clear(self) -> None:
        """清空所有注册项（不触发 disposer）"""
        self._items.clear()
