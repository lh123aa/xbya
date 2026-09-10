"""配置驱动的插件加载器

DSH「profile + 有序 patch 组装插件树」思想的 Python 简体版：
从 config.yaml 读取插件清单，按依赖拓扑排序后逐个导入并挂载到 Context。

设计取舍（对比 DSH）：
- 保留：配置驱动、依赖拓扑排序、单个插件失败不影响其他、可卸载
- 简化：不做多层 patch 合并（YAML 覆盖足够）、不做响应式依赖追踪

装配流程：
    parse_config(cfg) → [PluginSpec]
        → resolve_order(specs) → 拓扑有序的 [PluginSpec]
            → load_all(specs, ctx) → 逐个 import + 调用入口
"""

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.kernel.context import Context
from core.kernel.service import (
    CircularDependencyError,
    ConfigError,
    MissingDependencyError,
)

logger = logging.getLogger(__name__)

#: 插件清单在配置中的默认路径
DEFAULT_CONFIG_PATH = "agent.plugins"


@dataclass
class PluginSpec:
    """单个插件的声明

    Attributes:
        id: 插件唯一标识（用于依赖引用与日志）
        module: 模块路径（如 "agent.plugins.file_tools_plugin"）
        entry: 模块内入口函数名（签名 `(ctx, **config) -> Optional[Callable]`）
        config: 传给入口函数的配置
        enabled: 是否启用
        depends_on: 依赖的插件 id 列表
    """

    id: str
    module: str = ""
    entry: str = "setup"
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    depends_on: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PluginSpec":
        """从配置字典构造

        Raises:
            ConfigError: 缺少必填字段或字段类型错误
        """
        if not isinstance(raw, dict):
            raise ConfigError(f"插件配置项必须是字典，收到 {type(raw).__name__}")

        plugin_id = str(raw.get("id") or "").strip()
        if not plugin_id:
            raise ConfigError("插件配置缺少 id")

        module = str(raw.get("module") or "").strip()
        if not module:
            raise ConfigError(f"插件 '{plugin_id}' 缺少 module")

        entry_raw = raw.get("entry", "setup")
        if entry_raw is None or not str(entry_raw).strip():
            raise ConfigError(f"插件 '{plugin_id}' 的 entry 为空")
        entry = str(entry_raw).strip()

        config_raw = raw.get("config", {})
        if config_raw is None:
            config_raw = {}
        if not isinstance(config_raw, dict):
            raise ConfigError(f"插件 '{plugin_id}' 的 config 必须是字典")

        depends = raw.get("depends_on") or []
        if isinstance(depends, str):
            depends = [depends]
        if not isinstance(depends, list):
            raise ConfigError(f"插件 '{plugin_id}' 的 depends_on 必须是列表")

        return cls(
            id=plugin_id,
            module=module,
            entry=entry,
            config=dict(config_raw),
            enabled=bool(raw.get("enabled", True)),
            depends_on=[str(d) for d in depends],
        )

    def __repr__(self) -> str:
        return f"<PluginSpec {self.id} → {self.module}.{self.entry}>"


class PluginLoader:
    """插件加载器

    用法：
        loader = PluginLoader()
        specs = loader.parse_config(config_dict)          # 读配置
        order = loader.resolve_order(specs)                # 拓扑排序
        loaded, failed = loader.load_all(order, ctx)       # 逐个挂载
        loader.unload("file_tools", ctx)                   # 卸载
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH) -> None:
        """
        Args:
            config_path: 插件清单在配置字典中的路径（点号分隔）
        """
        self._config_path = config_path
        self._loaded: Dict[str, PluginSpec] = {}
        self._disposers: Dict[str, List[Callable[[], None]]] = {}

    # ══════════════════════════════════════════════
    #  配置解析
    # ══════════════════════════════════════════════

    def parse_config(self, config: Any) -> List[PluginSpec]:
        """解析配置中的插件清单

        Args:
            config: 完整配置字典，或插件清单列表本身

        Returns:
            启用状态的 PluginSpec 列表（保持配置顺序）

        Raises:
            ConfigError: 配置结构非法
        """
        raw_list = self._extract_list(config)

        specs: List[PluginSpec] = []
        seen: set = set()
        for raw in raw_list:
            spec = PluginSpec.from_dict(raw)
            if not spec.enabled:
                logger.debug("[loader] 跳过已禁用插件: %s", spec.id)
                continue
            if spec.id in seen:
                raise ConfigError(f"插件 id 重复: {spec.id}")
            seen.add(spec.id)
            specs.append(spec)

        logger.info("[loader] 解析出 %d 个启用插件", len(specs))
        return specs

    def _extract_list(self, config: Any) -> List[Any]:
        """从配置中取出插件清单列表"""
        if config is None:
            return []
        if isinstance(config, list):
            return config
        if not isinstance(config, dict):
            raise ConfigError(f"配置必须是字典或列表，收到 {type(config).__name__}")

        node: Any = config
        for key in self._config_path.split("."):
            if not isinstance(node, dict) or key not in node:
                return []
            node = node[key]

        if node is None:
            return []
        if not isinstance(node, list):
            raise ConfigError(f"{self._config_path} 必须是列表")
        return node

    # ══════════════════════════════════════════════
    #  依赖排序
    # ══════════════════════════════════════════════

    def resolve_order(self, specs: List[PluginSpec]) -> List[PluginSpec]:
        """按依赖拓扑排序（Kahn 算法）

        Args:
            specs: 插件列表

        Returns:
            依赖项在前的有序列表；无依赖时保持原顺序

        Raises:
            MissingDependencyError: 依赖的插件不存在
            CircularDependencyError: 存在依赖环
        """
        by_id = {s.id: s for s in specs}
        ids = [s.id for s in specs]

        # 1. 校验依赖存在性
        for spec in specs:
            for dep in spec.depends_on:
                if dep not in by_id:
                    raise MissingDependencyError(spec.id, dep)

        # 2. 计算入度（依赖数）
        indegree = {s.id: len(set(s.depends_on)) for s in specs}
        # 依赖 → 依赖者列表
        dependents: Dict[str, List[str]] = {s.id: [] for s in specs}
        for spec in specs:
            for dep in set(spec.depends_on):
                dependents[dep].append(spec.id)

        # 3. 入度为 0 的按原顺序入队（保证稳定排序）
        queue = [i for i in ids if indegree[i] == 0]
        ordered: List[PluginSpec] = []

        while queue:
            current = queue.pop(0)
            ordered.append(by_id[current])
            for dependent in dependents[current]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        # 4. 环检测
        if len(ordered) != len(specs):
            remaining = [i for i in ids if indegree[i] > 0]
            raise CircularDependencyError(self._find_cycle(by_id, remaining))

        return ordered

    @staticmethod
    def _find_cycle(by_id: Dict[str, PluginSpec], candidates: List[str]) -> List[str]:
        """在候选集合中找出一条具体的环路径（用于报错信息）"""
        allowed = set(candidates)
        for start in candidates:
            path: List[str] = []
            seen: set = set()
            node: Optional[str] = start
            # 顺着依赖一路走，直到回到走过的节点（成环）或走到尽头
            while node is not None and node in allowed:
                if node in seen:
                    idx = path.index(node)
                    return path[idx:] + [node]
                path.append(node)
                seen.add(node)
                nxt = None
                for dep in by_id[node].depends_on:
                    if dep in allowed:
                        nxt = dep
                        break
                node = nxt
        # 理论上不可达：候选集合为空时由调用方保证非空
        return candidates + [candidates[0]]  # pragma: no cover

    # ══════════════════════════════════════════════
    #  加载 / 卸载
    # ══════════════════════════════════════════════

    def load(self, spec: PluginSpec, ctx: Context) -> bool:
        """加载单个插件

        入口函数签名：`entry(ctx, **config)`；
        返回值可以是 disposer（函数）或 None。

        注册落在**共享上下文**上（插件提供的服务对全应用可见）。
        入口期间产生的所有注册（provide/on/once）会被"收编"到本插件名下：

        - 入口抛异常 → 立即逆序撤销这些注册（不留半成品）
        - unload()    → 逆序撤销这些注册 + 调用入口返回的 disposer

        需要注册隔离的插件可自行调用 `ctx.fork()`（此时子上下文的注册
        不在收编范围内，由插件自行 dispose）。

        Returns:
            True=加载成功；False=失败（已记录日志，不向调用方抛异常）
        """
        try:
            module = importlib.import_module(spec.module)
        except Exception as e:
            logger.error("[loader] 插件 '%s' 模块导入失败 (%s): %s", spec.id, spec.module, e)
            return False

        entry = getattr(module, spec.entry, None)
        if entry is None or not callable(entry):
            logger.error(
                "[loader] 插件 '%s' 的入口 '%s.%s' 不存在或不可调用",
                spec.id, spec.module, spec.entry,
            )
            return False

        # 记录入口前的注册点，用于收编/回滚
        mark = ctx.mark()
        try:
            result = entry(ctx, **spec.config)
        except Exception as e:
            logger.error("[loader] 插件 '%s' 入口执行失败: %s", spec.id, e, exc_info=True)
            self._run_disposers(ctx.reclaim(mark))
            return False

        owned = ctx.reclaim(mark)
        if callable(result):
            owned.append(result)

        self._loaded[spec.id] = spec
        self._disposers[spec.id] = owned
        logger.info(
            "[loader] ✓ 插件已加载: %s (%s, 收编 %d 项注册)",
            spec.id, spec.module, len(owned),
        )
        return True

    @staticmethod
    def _run_disposers(disposers: List[Callable[[], None]]) -> int:
        """逆序执行 disposer 列表（单个失败不中断）

        Returns:
            成功执行的数量
        """
        ok = 0
        for dispose in reversed(disposers):
            try:
                dispose()
                ok += 1
            except Exception as e:
                logger.debug("[loader] disposer 执行出错: %s", e)
        return ok

    def load_all(
        self,
        specs: List[PluginSpec],
        ctx: Context,
    ) -> Tuple[List[str], List[str]]:
        """按序加载全部插件

        Returns:
            (成功的 id 列表, 失败的 id 列表)；单个失败不中断其他
        """
        ok: List[str] = []
        failed: List[str] = []
        for spec in specs:
            if self.load(spec, ctx):
                ok.append(spec.id)
            else:
                failed.append(spec.id)

        if failed:
            logger.warning("[loader] %d 个插件加载失败: %s", len(failed), failed)
        logger.info("[loader] 加载完成：成功 %d，失败 %d", len(ok), len(failed))
        return ok, failed

    def unload(self, plugin_id: str, ctx: Context = None) -> bool:
        """卸载插件（逆序撤销其注册 + 调用入口返回的 disposer）

        Returns:
            True=已卸载或本就不存在；False=卸载过程出错
        """
        disposers = self._disposers.pop(plugin_id, None)
        self._loaded.pop(plugin_id, None)

        if not disposers:
            logger.debug("[loader] 插件未加载，无需卸载: %s", plugin_id)
            return True

        logger.info("[loader] 卸载插件 %s（撤销 %d 项注册）", plugin_id, len(disposers))
        ok = True
        for dispose in reversed(disposers):
            try:
                dispose()
            except Exception as e:
                logger.error("[loader] 卸载插件 '%s' 时出错: %s", plugin_id, e)
                ok = False

        return ok

    def unload_all(self) -> int:
        """卸载全部插件（逆序），返回卸载数量"""
        ids = list(self._loaded.keys())
        count = 0
        for plugin_id in reversed(ids):
            if self.unload(plugin_id):
                count += 1
        return count

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    def loaded_ids(self) -> List[str]:
        """已加载的插件 id 列表"""
        return list(self._loaded.keys())

    def is_loaded(self, plugin_id: str) -> bool:
        """插件是否已加载"""
        return plugin_id in self._loaded

    def spec_of(self, plugin_id: str) -> Optional[PluginSpec]:
        """已加载插件的声明"""
        return self._loaded.get(plugin_id)

    def count(self) -> int:
        """已加载插件数量"""
        return len(self._loaded)

    def __repr__(self) -> str:
        return f"<PluginLoader loaded={len(self._loaded)}>"
