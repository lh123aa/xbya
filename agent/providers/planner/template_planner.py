"""规则配方规划器（TemplatePlanner）

DSH 能力 Seam 三角的 Provider：
- Definition: agent/seams/planner.py
- Provider:   本模块（确定性配方）
- Consumer:   agent/pipeline.py（经 HybridPlanner 消费）

## 为什么先做规则规划

「先找到合同再挪到归档」这类话里，**动作词序列是显式给出的** ——
先搜索、后移动，两个动作都能在规则词表里对上。为这种句子花一次 LLM 调用
（1~3s + 配额）是浪费，而且 LLM 还有拆错的风险。规则配方 <1ms 且**结果确定**。

LLM 规划器（llm_planner.py）负责的是规则覆盖不到的、需要真语义理解的说法
（"把下载目录收拾一下"），两者是互补而非替代 —— 与路由层 rule→llm 的分工一致。

## 配方必须贴着工具的真实契约写

本规划器产出的参数会**直接**交给工具执行，所以每条配方都按工具 schema 对齐。
两个容易踩的坑（已按实际 schema 处理）：

- `file_move` 的入参是**单个** `source`，不是列表 → 只能挪第一个匹配项，
  故配方里写 `${s1.paths.0}` 取首项
- `file_delete` 的入参是**列表** `targets` → 可以整批交出 `${s1.paths}`

## 词表复用

目录别名与扩展名别名直接复用 `rule_router` 的 `DIR_ALIASES` / `EXT_ALIASES`：
这两张表是"用户口语 → 内部取值"的**共享词汇**，两处各存一份必然漂移。
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from agent.providers.router.rule_router import (
    DIR_ALIASES,
    EXT_ALIASES,
    TIME_ALIASES,
)
from agent.seams.planner import (
    MAX_PLAN_STEPS,
    Plan,
    PlannerService,
    PlanStep,
)

logger = logging.getLogger(__name__)

#: 「归档」的默认落点：用户说"归档"但没指明目录时，落到文档目录
DEFAULT_ARCHIVE_DIR = "Documents"

#: 搜索类动词（用于判断动作先后顺序）
SEARCH_VERBS: Tuple[str, ...] = (
    "找一下", "找找", "搜一下", "搜索", "查找", "查询", "找", "搜",
    "有没有", "哪里有", "看看有没有",
)

#: 移动类动词
MOVE_VERBS: Tuple[str, ...] = (
    "挪到", "挪进", "移到", "移进", "移动到", "放到", "放进去", "归档",
)

#: 删除类动词
DELETE_VERBS: Tuple[str, ...] = ("删掉", "删除", "清理", "清空", "删了", "不要了")

#: 读取类动词
READ_VERBS: Tuple[str, ...] = ("打开", "读一下", "读读", "查看", "看看", "的内容")

#: 翻译类动词
TRANSLATE_VERBS: Tuple[str, ...] = ("翻译", "译成", "翻成", "译为", "翻译成")

#: 截图类动词
SCREENSHOT_VERBS: Tuple[str, ...] = ("截图", "截屏", "屏幕截图", "截个图")

#: 打开类动词（与 READ_VERBS 有重叠，意图由配方上下文区分）
OPEN_VERBS: Tuple[str, ...] = ("打开", "启动", "查看")

#: 目标语言别名 → translate 工具的 target_lang 枚举值
LANG_ALIASES: Dict[str, str] = {
    "英文": "en", "英语": "en", "english": "en",
    "中文": "zh", "汉语": "zh", "chinese": "zh",
    "日文": "ja", "日语": "ja", "japanese": "ja",
    "韩文": "ko", "韩语": "ko", "korean": "ko",
    "法文": "fr", "法语": "fr", "french": "fr",
    "德文": "de", "德语": "de", "german": "de",
    "俄文": "ru", "俄语": "ru", "russian": "ru",
}

#: 抽名词短语时要剥掉的噪声词（长词优先替换，避免"文件"吃掉"文件夹"）
#:
#: 「找到」必须在「到」之前 —— 否则"找到合同"会先被切掉"找"再留下"到合同"，
#: 产出 `*到合同*` 这种永远搜不到的模式。
_PLAN_NOISE: List[str] = [
    "帮我", "给我", "麻烦", "请你", "请", "我的", "我",
    "一下", "一个", "那个", "这个", "那些", "这些",
    "关于", "有关", "所有", "全部",
    "文件夹", "文件", "文档", "东西",
    "找到", "找找", "看看", "出来", "起来",
    "的", "上", "里", "中", "在", "把", "将", "有",
    "到", "了", "个", "些", "好",
] + list(DIR_ALIASES.keys())

#: 引号包裹的名字最可信（中英文引号都要认）
_QUOTE_RE = re.compile(r"[「『“\"']([^」』”\"']{1,30})[」』”\"']")

#: 连接词 —— 用来切出"第一个动作子句"的范围
_CLAUSE_SPLIT_RE = re.compile(r"然后|接着|之后|完了再|最后再|最后|再|并且|同时|，|,|。|\s")


def _has_any(norm: str, verbs: Sequence[str]) -> bool:
    """文本是否含任一动词"""
    return any(v in norm for v in verbs)


def _first_pos(norm: str, verbs: Sequence[str]) -> int:
    """任一动词最早出现的位置；没有则返回 -1

    用来判断动作先后 —— "先找到合同再挪到归档" 是「搜→移」，
    而 "把合同挪到归档再找一下" 是「移→搜」，两条配方不应互抢。
    """
    best = -1
    for v in verbs:
        idx = norm.find(v)
        if idx >= 0 and (best < 0 or idx < best):
            best = idx
    return best


def _ordered(norm: str, first: Sequence[str], second: Sequence[str]) -> bool:
    """first 类动词是否出现在 second 类动词之前"""
    a = _first_pos(norm, first)
    b = _first_pos(norm, second)
    return a >= 0 and b >= 0 and a < b


def _strip_noise(text: str) -> str:
    """剥掉噪声词与目录别名，留下核心名词"""
    s = text
    for w in sorted(_PLAN_NOISE, key=len, reverse=True):
        s = s.replace(w, "")
    return s.strip(" 　的在和与及、,。.!！?？")


def _clause_after(norm: str, verbs: Sequence[str], limit: int = 24) -> str:
    """取某个动词之后、下一个连接词之前的子句"""
    idx = _first_pos(norm, verbs)
    if idx < 0:
        return ""
    for v in sorted(verbs, key=len, reverse=True):
        pos = norm.find(v)
        if pos == idx:
            idx += len(v)
            break
    tail = norm[idx: idx + limit]
    # maxsplit=1 保证结果非空，省掉一个"结构上不可达"的空列表分支
    return _CLAUSE_SPLIT_RE.split(tail, maxsplit=1)[0].strip()


def _extract_pattern(
    norm: str,
    raw: str,
    verbs: Sequence[str] = SEARCH_VERBS,
) -> Optional[str]:
    """抽出文件名 glob 模式

    优先级：引号内的名字 > 扩展类别别名 > 动词之后的名词短语

    Args:
        norm: 归一化文本
        raw: 原文（引号匹配需要保留原始大小写与标点）
        verbs: 从哪个动作动词之后取名词短语（搜索类配方传 READ_VERBS）
    """
    m = _QUOTE_RE.search(raw or "") or _QUOTE_RE.search(norm)
    if m:
        name = m.group(1).strip()
        if name:
            return f"*{name}*"

    for alias in sorted(EXT_ALIASES.keys(), key=len, reverse=True):
        if alias in norm:
            return EXT_ALIASES[alias]

    core = _strip_noise(_clause_after(norm, verbs))
    if 1 <= len(core) <= 16:
        return f"*{core}*"
    return None


def _extract_dirs(norm: str) -> List[str]:
    """抽出涉及的目录（去重，保持别名表顺序）"""
    dirs: List[str] = []
    for alias, name in DIR_ALIASES.items():
        if alias in norm and name not in dirs:
            dirs.append(name)
    return dirs


def _extract_time_range(norm: str) -> Optional[str]:
    """抽出时间范围"""
    for alias in sorted(TIME_ALIASES.keys(), key=len, reverse=True):
        if alias in norm:
            return TIME_ALIASES[alias]
    return None


#: 移动目标里的**显式路径**（Windows 盘符 / UNC / ~ / 空格后的绝对路径）
#:
#: 用户在"挪到 X"里写路径时必须原样交给安全层判断，**不能**退回目录别名去猜：
#: 「挪到 C:\Windows\System32」若被猜成 Desktop，计划会"成功"地把文件挪到
#: 用户没要求的地方 —— 静默做错事，比明确拒绝危险得多。
_EXPLICIT_PATH_RE = re.compile(
    r"(?:[a-zA-Z]:[\\/]|\\\\|~[\\/]|(?<=\s)/)[^\s，,。;；、]*"
)


def _verb_end(norm: str, verbs: Sequence[str]) -> int:
    """最早出现的动词的**结束**位置；没有则返回 -1

    一次遍历同时求「最早位置」与「该位置上最长的动词」。
    分两步做（先求位置、再回头按位置找动词）会留下一个"找不到"的兜底分支 ——
    而按 `_first_pos` 的构造那个分支不可能发生，等于写一行永远测不到的死代码。
    """
    best_at = -1
    best_end = -1
    for v in verbs:
        pos = norm.find(v)
        if pos < 0:
            continue
        end = pos + len(v)
        # 位置更早者优先；同一位置取更长的动词
        # （"找一下" 与 "找" 都在下标 0 时，要把「找一下」整体吃掉）
        if best_at < 0 or pos < best_at or (pos == best_at and end > best_end):
            best_at, best_end = pos, end
    return best_end


def _explicit_path_after_move(norm: str) -> Optional[str]:
    """移动动词之后是否跟着一个显式路径；没有则返回 None"""
    start = _verb_end(norm, MOVE_VERBS)
    if start < 0:
        return None
    m = _EXPLICIT_PATH_RE.search(norm[start:])
    return m.group(0).strip() if m else None


def _extract_dest(norm: str) -> Optional[str]:
    """抽出移动目标目录

    优先级：
    1. **显式路径** —— 用户说了路径就照做（是否允许由安全守卫裁决）
    2. 移动动词后的子句里的目录别名
    3. 整句里的目录别名（兜底）

    第 2/3 步必须分两轮：先看移动动词后的子句，再退回整句 ——
    "先找到桌面上的压缩包然后再挪到文档" 里同时出现"桌面"和"文档"，
    若只扫整句，按别名表顺序会先命中"桌面"，于是文件被挪到桌面，
    而用户说的是文档。这类"挪错目录"是真实世界的误操作。

    「归档」是动词而非目录名，用户说"挪到归档"时落点需要一个默认值，
    否则这条配方会因为缺 dest 而无法成立（而它恰恰是最常用的说法）。
    """
    explicit = _explicit_path_after_move(norm)
    if explicit:
        return explicit

    tail = _clause_after(norm, MOVE_VERBS, limit=12)
    for source in (tail, norm):
        if not source:
            continue
        for alias in sorted(DIR_ALIASES.keys(), key=len, reverse=True):
            if alias in source:
                return DIR_ALIASES[alias]
    if "归档" in norm:
        return DEFAULT_ARCHIVE_DIR
    return None


def _extract_lang(norm: str) -> Optional[str]:
    """抽出目标语言（translate 工具的 target_lang 枚举值）"""
    for alias in sorted(LANG_ALIASES.keys(), key=len, reverse=True):
        if alias in norm:
            return LANG_ALIASES[alias]
    return None


# ══════════════════════════════════════════════
#  配方
# ══════════════════════════════════════════════


@dataclass(slots=True)
class Recipe:
    """一条多步配方

    Attributes:
        recipe_id: 配方标识（日志与测试用）
        name: 人类可读名称
        description: 用途说明（供 LLM 规划器复用作提示）
        requires: 必须**全部**可用才启用本配方（缺任一工具则不生成该计划）
        matches: 判定函数 (norm) -> bool
        build: 构造函数 (norm, raw) -> Optional[List[PlanStep]]
        priority: 优先级，大的先试（同一句话可能命中多条配方）
    """

    recipe_id: str
    name: str
    description: str
    requires: Tuple[str, ...]
    matches: Callable[[str], bool]
    build: Callable[[str, str], Optional[List[PlanStep]]]
    priority: int = 0


def _build_search_move(norm: str, raw: str) -> Optional[List[PlanStep]]:
    """搜 → 移（只挪第一个匹配项，因为 file_move 收单个 source）"""
    dest = _extract_dest(norm)
    if not dest:
        return None

    search_params: Dict[str, Any] = {}
    pattern = _extract_pattern(norm, raw)
    if pattern:
        search_params["pattern"] = pattern
    dirs = _extract_dirs(norm)
    if dirs:
        search_params["dirs"] = dirs
    if not search_params:
        return None

    return [
        PlanStep(
            action="file_search",
            params=search_params,
            step_id="s1",
            description="先找到目标文件",
        ),
        PlanStep(
            action="file_move",
            params={"source": "${s1.paths.0}", "dest": dest},
            step_id="s2",
            description=f"把它挪到 {dest}",
            depends_on=["s1"],
        ),
    ]


def _build_search_delete(norm: str, raw: str) -> Optional[List[PlanStep]]:
    """搜 → 删（整批，file_delete 收列表 targets）"""
    search_params: Dict[str, Any] = {}
    pattern = _extract_pattern(norm, raw)
    if pattern:
        search_params["pattern"] = pattern
    dirs = _extract_dirs(norm)
    if dirs:
        search_params["dirs"] = dirs
    time_range = _extract_time_range(norm)
    if time_range:
        search_params["time_range"] = time_range
    if not search_params:
        return None

    return [
        PlanStep(
            action="file_search",
            params=search_params,
            step_id="s1",
            description="先找出要清理的文件",
        ),
        PlanStep(
            action="file_delete",
            params={"targets": "${s1.paths}"},
            step_id="s2",
            description="把它们移入回收站",
            depends_on=["s1"],
        ),
    ]


def _build_search_read(norm: str, raw: str) -> Optional[List[PlanStep]]:
    """搜 → 读"""
    search_params: Dict[str, Any] = {}
    pattern = _extract_pattern(norm, raw)
    if pattern:
        search_params["pattern"] = pattern
    dirs = _extract_dirs(norm)
    if dirs:
        search_params["dirs"] = dirs
    if not search_params:
        return None

    return [
        PlanStep(
            action="file_search",
            params=search_params,
            step_id="s1",
            description="先找到文件",
        ),
        PlanStep(
            action="file_read",
            params={"target": "${s1.paths.0}"},
            step_id="s2",
            description="打开它看看内容",
            depends_on=["s1"],
        ),
    ]


def _build_read_translate(norm: str, raw: str) -> Optional[List[PlanStep]]:
    """读 → 译（file_read 的产出字段是 content）

    注意这里从**读取动词**之后取名词，不是搜索动词 ——
    "读一下合同然后翻译成英文"里没有搜索动词，按搜索动词切会得到空串。
    """
    pattern = _extract_pattern(norm, raw, READ_VERBS)
    if not pattern:
        return None

    translate_params: Dict[str, Any] = {"text": "${s1.content}"}
    lang = _extract_lang(norm)
    if lang:
        translate_params["target_lang"] = lang

    return [
        PlanStep(
            action="file_read",
            params={"target": pattern},
            step_id="s1",
            description="先读到内容",
        ),
        PlanStep(
            action="translate",
            params=translate_params,
            step_id="s2",
            description="再翻译一遍",
            depends_on=["s1"],
        ),
    ]


def _build_screenshot_open(norm: str, raw: str) -> Optional[List[PlanStep]]:
    """截 → 开"""
    return [
        PlanStep(
            action="screenshot",
            params={},
            step_id="s1",
            description="先截个图",
        ),
        PlanStep(
            action="open_app",
            params={"target": "${s1.path}"},
            step_id="s2",
            description="打开刚截的图",
            depends_on=["s1"],
        ),
    ]


#: 内置配方表（按 priority 降序尝试）
#:
#: 顺序有意设计：`search_delete` 先于 `search_move`，因为"清理"类说法
#: 常同时含"挪"字（"挪走不要的"），删除意图更强时应优先命中。
DEFAULT_RECIPES: List[Recipe] = [
    Recipe(
        recipe_id="search_delete",
        name="搜索后清理",
        description="先搜索符合条件的文件，再整批移入回收站",
        requires=("file_search", "file_delete"),
        matches=lambda n: _ordered(n, SEARCH_VERBS, DELETE_VERBS),
        build=_build_search_delete,
        priority=30,
    ),
    Recipe(
        recipe_id="search_move",
        name="搜索后归档",
        description="先搜索目标文件，再把它移动到归档目录",
        requires=("file_search", "file_move"),
        matches=lambda n: _ordered(n, SEARCH_VERBS, MOVE_VERBS),
        build=_build_search_move,
        priority=25,
    ),
    Recipe(
        recipe_id="search_read",
        name="搜索后查看",
        description="先搜索文件，再打开读取内容",
        requires=("file_search", "file_read"),
        matches=lambda n: _ordered(n, SEARCH_VERBS, READ_VERBS),
        build=_build_search_read,
        priority=20,
    ),
    Recipe(
        recipe_id="read_translate",
        name="读取后翻译",
        description="先读取文件内容，再翻译成指定语言",
        requires=("file_read", "translate"),
        matches=lambda n: (
            _has_any(n, READ_VERBS)
            and _has_any(n, TRANSLATE_VERBS)
            and _first_pos(n, SEARCH_VERBS) < 0
        ),
        build=_build_read_translate,
        priority=15,
    ),
    Recipe(
        recipe_id="screenshot_open",
        name="截图后打开",
        description="先截取屏幕，再用默认程序打开这张截图",
        requires=("screenshot", "open_app"),
        matches=lambda n: (
            _has_any(n, SCREENSHOT_VERBS)
            and _has_any(n, OPEN_VERBS)
            and not _has_any(n, SEARCH_VERBS)
        ),
        build=_build_screenshot_open,
        priority=10,
    ),
]


class TemplatePlanner(PlannerService):
    """规则配方规划器（<1ms，结果确定）

    用法：
        planner = TemplatePlanner(available_actions=registry.names())
        plan = planner.plan("先找到合同然后再挪到归档")
        # Plan(template) 2步: file_search → file_move
    """

    capability_name = "planner"
    provider_name = "template"

    def __init__(
        self,
        available_actions: Optional[Sequence[str]] = None,
        recipes: Optional[List[Recipe]] = None,
        max_steps: int = MAX_PLAN_STEPS,
    ) -> None:
        """
        Args:
            available_actions: 当前可用工具名；缺工具时相关配方自动停用
            recipes: 配方表；None 使用 DEFAULT_RECIPES
            max_steps: 步骤数上限
        """
        self._available = list(available_actions) if available_actions else []
        self._recipes = sorted(
            recipes if recipes is not None else DEFAULT_RECIPES,
            key=lambda r: -r.priority,
        )
        self._max_steps = max(1, int(max_steps))

    # ══════════════════════════════════════════════
    #  PlannerService
    # ══════════════════════════════════════════════

    def plan(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Plan]:
        """按配方表把一句话拆成计划

        Returns:
            计划；没有配方命中或产出步骤不足 2 步时返回 None
        """
        try:
            return self._plan_impl(text, context or {})
        except Exception as e:                       # 规划器不得抛异常
            logger.warning("[planner/template] 规划失败: %s", e, exc_info=True)
            return None

    def can_plan(self, text: str) -> bool:
        """是否有配方在形式上匹配（不构造计划，故极快）

        注意必须把**本实例的工具表**传进 `_recipe_usable`：
        不传会让它走"工具表未知 → 一律放行"的宽松兜底，
        于是缺工具的配方也会被报成"可规划"，与 plan() 的结论自相矛盾。
        """
        norm = self._normalize(text)
        if not norm:
            return False
        return any(
            self._recipe_usable(r, self._available) and r.matches(norm)
            for r in self._recipes
        )

    def describe_recipes(self) -> List[Dict[str, Any]]:
        """导出配方描述（供 LLM 规划器复用作提示）"""
        return [
            {
                "recipe_id": r.recipe_id,
                "name": r.name,
                "description": r.description,
                "requires": list(r.requires),
            }
            for r in self._recipes
        ]

    # ══════════════════════════════════════════════
    #  内部
    # ══════════════════════════════════════════════

    def _plan_impl(self, text: str, context: Dict[str, Any]) -> Optional[Plan]:
        """真正干活的部分（异常由 plan() 兜住）"""
        norm = self._normalize(text)
        if not norm:
            return None

        # 上下文里带的最新工具表优先（装配后工具集可能变化）
        available = context.get("available_actions") or self._available

        for recipe in self._recipes:
            if not self._recipe_usable(recipe, available):
                continue
            if not recipe.matches(norm):
                continue

            steps = recipe.build(norm, str(text or ""))
            if not steps:
                logger.debug("[planner/template] 配方 %s 命中但构造失败", recipe.recipe_id)
                continue

            plan = Plan(
                goal=str(text or ""),
                steps=steps,
                source=self.provider_name,
            ).truncate(self._max_steps)

            if not plan.is_multi_step():
                logger.debug("[planner/template] 配方 %s 只产出单步，忽略",
                             recipe.recipe_id)
                continue

            logger.info("[planner/template] 配方 %s → %s", recipe.recipe_id, plan)
            return plan

        return None

    def _recipe_usable(
        self,
        recipe: Recipe,
        available: Optional[Sequence[str]] = None,
    ) -> bool:
        """配方所需工具是否都在可用表里

        `available` 为空表示"不知道可用工具"，此时**放行**该配方：
        计划产出后管线仍会用真实注册表核对，缺工具会在执行期明确失败，
        比在这里默默什么都不做更容易排查。
        """
        names = list(available) if available else []
        if not names:
            return True
        return all(a in names for a in recipe.requires)

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化：转小写、合并空白"""
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()
