"""P3-A —— 多步任务规划（planner seam / D5）测试

覆盖四层：
1. **seam 契约**（`agent/seams/planner.py`）—— 数据结构与占位符解析
2. **三个 Provider**（template / llm / hybrid）
3. **规则路由的 plan 意图**（`_route_multi_step`）—— 且不扰动既有 73 条测试集
4. **管线多步执行**（顺序执行 / 逐步安全校验 / 确认挂起恢复 / 取消 / 打断 / 中途失败）

第 4 层用真实 ThreadPool 执行器 + 真实 BasicGuard + 假工具，
所以"挂起→批准→从断点续跑"是**真的**跑了一遍，而不是断言某个 mock 被调用。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.message import AgentCommand
from agent.pipeline import AgentPipeline, _PlanRun
from agent.plugins import SVC_CONFIG, SVC_LLM_ONCE, SVC_PLANNER
from agent.plugins.planner_plugin import setup as planner_plugin_setup
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.planner import HybridPlanner, LLMPlanner, TemplatePlanner
from agent.providers.planner import template_planner as tp
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.seams import planner as seam
from agent.seams.planner import (
    MAX_PLAN_STEPS,
    Plan,
    PlannerService,
    PlanStatus,
    PlanStep,
    extract_field,
    resolve_placeholders,
)
from agent.tools.base import BaseTool, ToolResult
from agent.tools.file_tools import FileMoveTool
from agent.tools.memory_tools import MemoryRecallTool
from agent.tools.registry import ToolRegistry
from core.kernel.context import Context
from core.kernel.events import EventBus, EventTypes

ALL_ACTIONS = [
    "file_search", "file_list", "file_read", "file_rename", "file_move",
    "file_delete", "system_info", "clipboard", "open_app", "screenshot",
    "run_command", "calculate", "translate", "reminder", "weather",
    "web_open", "web_search", "web_read",
]


# ══════════════════════════════════════════════════
#  1. seam 契约
# ══════════════════════════════════════════════════

class TestPlaceholderResolution:
    """占位符解析：Provider 与 Consumer 共用的数据传递机制"""

    RESULTS = {
        "s1": [
            {"name": "a.zip", "path": "/tmp/a.zip"},
            {"name": "b.zip", "path": "/tmp/b.zip"},
        ],
        "s2": {"count": 3, "content": "正文", "name": "doc"},
    }

    def test_whole_value_keeps_native_type(self):
        """整个取值是单个占位符 → 列表仍是列表（不能字符串化）"""
        out = resolve_placeholders({"targets": "${s1.paths}"}, self.RESULTS)
        assert out["targets"] == ["/tmp/a.zip", "/tmp/b.zip"]

    def test_embedded_value_is_stringified(self):
        """占位符嵌在文本中 → 拼接成字符串"""
        out = resolve_placeholders({"msg": "找到 ${s1.paths}"}, self.RESULTS)
        assert out["msg"] == "找到 /tmp/a.zip、/tmp/b.zip"

    def test_embedded_scalar_is_stringified(self):
        """标量占位符嵌入文本时转字符串（列表走的是另一条拼接分支）"""
        out = resolve_placeholders({"msg": "共 ${s2.count} 个"}, self.RESULTS)
        assert out["msg"] == "共 3 个"

    def test_unresolvable_keeps_original(self):
        """解析不出来时保留原文，绝不抛异常"""
        out = resolve_placeholders({"t": "${nope.x}", "p": "前缀 ${s1.zzz}"},
                                  self.RESULTS)
        assert out["t"] == "${nope.x}"
        assert out["p"] == "前缀 ${s1.zzz}"

    def test_last_alias(self):
        out = resolve_placeholders({"t": "${last.paths}"}, {"last": ["x"]})
        assert out["t"] == ["x"]

    def test_index_and_nested_field(self):
        out = resolve_placeholders({"a": "${s1.0.path}", "b": "${s2.content}"},
                                  self.RESULTS)
        assert out["a"] == "/tmp/a.zip"
        assert out["b"] == "正文"

    def test_nested_params_are_walked(self):
        """占位符可以出现在嵌套 dict / list 里"""
        out = resolve_placeholders(
            {"outer": {"inner": ["${s2.count}"]}, "d": {"k": "${s1.paths}"}},
            self.RESULTS,
        )
        assert out["outer"]["inner"] == [3]
        assert out["d"]["k"] == ["/tmp/a.zip", "/tmp/b.zip"]

    def test_original_params_not_mutated(self):
        params = {"t": "${s1.paths}"}
        resolve_placeholders(params, self.RESULTS)
        assert params["t"] == "${s1.paths}"

    def test_non_dict_params_returns_empty(self):
        assert resolve_placeholders(None, self.RESULTS) == {}
        assert resolve_placeholders("x", self.RESULTS) == {}

    def test_empty_results_returns_copy(self):
        params = {"t": "${s1.paths}"}
        out = resolve_placeholders(params, None)
        assert out == {"t": "${s1.paths}"}
        assert out is not params

    def test_plain_values_pass_through(self):
        out = resolve_placeholders(
            {"n": 1, "b": True, "none": None, "lst": [1, 2], "d": {"a": 1}},
            self.RESULTS,
        )
        assert out == {"n": 1, "b": True, "none": None, "lst": [1, 2], "d": {"a": 1}}


class TestExtractField:
    """extract_field 的取值规则"""

    def test_dict_key(self):
        assert extract_field({"k": 9}, "k") == 9
        assert extract_field({"k": 9}, "missing") is None

    def test_list_paths(self):
        # 条目缺 path 时回退到 name（与 pipeline._path_of 一致，便于裸名由工具在白名单内解析）
        items = [{"path": "/a", "name": "a"}, {"name": "b"}, "raw"]
        assert extract_field(items, "paths") == ["/a", "b", "raw"]

    def test_list_paths_skips_unusable_items(self):
        # 既不是 dict 也不是 str 的条目取不出路径 → 被过滤掉
        assert extract_field([1, None, "raw"], "paths") == ["raw"]

    def test_list_names(self):
        items = [{"name": "a"}, "b"]
        assert extract_field(items, "names") == ["a", "b"]

    def test_list_index(self):
        assert extract_field(["x", "y"], "1") == "y"
        assert extract_field(["x"], "5") is None

    def test_list_unknown_field(self):
        assert extract_field(["x"], "whatever") is None

    def test_scalar_returns_itself(self):
        assert extract_field("hello", "anything") == "hello"

    def test_empty_field_returns_value(self):
        assert extract_field({"k": 1}, "") == {"k": 1}


class TestPlanStepAndPlan:
    """PlanStep / Plan 的自检方法"""

    def test_step_params_non_dict_coerced(self):
        assert PlanStep(action="a", params=None).params == {}
        assert PlanStep(action="a", params=[1]).params == {}

    def test_step_refs_distinct_and_ordered(self):
        step = PlanStep(
            action="a",
            params={"t": "${s2.x}", "u": ["${s1.y}", "${s2.z}"], "n": 1},
        )
        assert step.refs() == ["s2", "s1"]

    def test_refs_ignores_non_string_and_plain(self):
        assert PlanStep(action="a", params={"n": 1, "s": "no ref"}).refs() == []

    def test_refs_skips_non_string_items_inside_containers(self):
        """容器里的非字符串项不参与匹配（深度遍历只产出字符串）"""
        step = PlanStep(action="a", params={"lst": ["${s1.y}", 5, None]})
        assert step.refs() == ["s1"]

    def test_plan_auto_ids(self):
        plan = Plan(steps=[PlanStep("a"), PlanStep("b", step_id="keep")])
        assert [s.step_id for s in plan.steps] == ["s1", "keep"]

    def test_plan_drops_non_steps(self):
        plan = Plan(steps=[PlanStep("a"), "junk", None])
        assert len(plan.steps) == 1

    def test_is_multi_step(self):
        assert Plan(steps=[PlanStep("a"), PlanStep("b")]).is_multi_step()
        assert not Plan(steps=[PlanStep("a")]).is_multi_step()

    def test_action_names_dedup(self):
        plan = Plan(steps=[PlanStep("a"), PlanStep("b"), PlanStep("a")])
        assert plan.action_names() == ["a", "b"]

    def test_truncate(self):
        plan = Plan(steps=[PlanStep("a")] * 10)
        assert len(plan.truncate(3)) == 3
        assert Plan(steps=[PlanStep("a")] * 2).truncate(0) is not None
        assert len(Plan(steps=[PlanStep("a")] * 2).truncate(0)) == 1   # 至少留 1 步

    def test_unknown_actions(self):
        plan = Plan(steps=[PlanStep("a"), PlanStep("zzz")])
        assert plan.unknown_actions(["a", "b"]) == ["zzz"]
        assert plan.unknown_actions([]) == ["a", "zzz"]

    def test_len_and_str(self):
        plan = Plan(steps=[PlanStep("a"), PlanStep("b")], source="rule")
        assert len(plan) == 2
        assert "rule" in str(plan) and "2步" in str(plan)

    def test_default_can_plan_and_recipes(self):
        """seam 的默认实现是保守的"""
        class Dummy(PlannerService):
            def plan(self, text, context=None):
                return None

        d = Dummy()
        assert d.can_plan("任意") is False
        assert d.describe_recipes() == []
        assert d.capability_name == "planner"

    def test_missing_sentinel_repr(self):
        """解析失败哨兵可读（仅调试可见）"""
        assert "MISSING" in repr(seam._MISSING)

    def test_lookup_edge_cases(self):
        assert seam._lookup("", {}) is seam._MISSING
        assert seam._lookup("s1", {"s1": 1}) == 1
        assert seam._lookup("nope.x", {"s1": 1}) is seam._MISSING
        assert seam._lookup("s1.deep.x", {"s1": {"deep": None}}) is seam._MISSING

    def test_status_enum_values(self):
        assert PlanStatus.HALTED.value == "halted"
        assert MAX_PLAN_STEPS >= 2


# ══════════════════════════════════════════════════
#  2a. TemplatePlanner
# ══════════════════════════════════════════════════

class TestTemplatePlannerHelpers:

    def test_has_any(self):
        assert tp._has_any("找文件", tp.SEARCH_VERBS)
        assert not tp._has_any("你好", tp.SEARCH_VERBS)

    def test_first_pos_and_ordered(self):
        assert tp._first_pos("先找再挪", tp.SEARCH_VERBS) == 1
        assert tp._first_pos("你好", tp.SEARCH_VERBS) == -1
        assert tp._ordered("先找再挪到", tp.SEARCH_VERBS, tp.MOVE_VERBS)
        assert not tp._ordered("挪到再找", tp.SEARCH_VERBS, tp.MOVE_VERBS)
        assert not tp._ordered("只找", tp.SEARCH_VERBS, tp.MOVE_VERBS)

    def test_strip_noise(self):
        assert tp._strip_noise("桌面上的合同文件") == "合同"
        assert tp._strip_noise("") == ""

    def test_clause_after_no_verb(self):
        assert tp._clause_after("你好", tp.SEARCH_VERBS) == ""

    def test_clause_after_cuts_at_connector(self):
        assert tp._clause_after("找一下合同然后挪到", tp.SEARCH_VERBS) == "合同"

    def test_extract_pattern_quote_beats_alias(self):
        # 引号里的名字优先于扩展类别别名
        assert tp._extract_pattern("找一下 pdf 「年度报告」", "找一下 pdf 「年度报告」") \
            == "*年度报告*"

    def test_extract_pattern_quote_in_normalized(self):
        # 引号只在归一化文本里（原文没引号）
        assert tp._extract_pattern("找一下「合同」", "找一下") == "*合同*"

    def test_extract_pattern_quote_empty_falls_through(self):
        assert tp._extract_pattern("找一下 pdf 「」", "找一下 pdf 「」") == "*.pdf"

    def test_extract_pattern_extension_alias(self):
        assert tp._extract_pattern("找一下压缩包", "找一下压缩包") == "*.zip"

    def test_extract_pattern_from_clause(self):
        assert tp._extract_pattern("找一下合同", "找一下合同") == "*合同*"

    def test_extract_pattern_too_long_returns_none(self):
        long_name = "一二三四五六七八九十十一十二十三十四十五"
        assert tp._extract_pattern(f"找一下{long_name}", "") is None

    def test_builders_return_none_when_nothing_extractable(self):
        """能抽出目标目录但抽不出文件名/目录 → 搜索参数为空 → 构造失败"""
        planner = TemplatePlanner(available_actions=ALL_ACTIONS)
        assert planner.plan("找到然后再挪到归档") is None

    def test_search_move_with_dirs(self):
        """搜索参数里带目录时也要落进计划"""
        planner = TemplatePlanner(available_actions=ALL_ACTIONS)
        plan = planner.plan("先找到桌面上的压缩包然后再挪到文档")
        assert plan.steps[0].params["dirs"] == ["Desktop", "Documents"]
        assert plan.steps[1].params["dest"] == "Documents"

    def test_search_read_without_searchable_params_returns_none(self):
        planner = TemplatePlanner(available_actions=ALL_ACTIONS)
        assert planner.plan("找到然后再打开") is None

    def test_read_translate_without_target_returns_none(self):
        planner = TemplatePlanner(available_actions=ALL_ACTIONS)
        assert planner.plan("读一下然后翻译成英文") is None

    def test_extract_pattern_empty_returns_none(self):
        assert tp._extract_pattern("找到", "") is None

    def test_extract_dirs_dedup_and_order(self):
        assert tp._extract_dirs("桌面和图片里的") == ["Desktop", "Pictures"]
        assert tp._extract_dirs("没有目录") == []

    def test_extract_time_range(self):
        assert tp._extract_time_range("昨天找的") == "yesterday"
        assert tp._extract_time_range("找的") is None

    def test_extract_dest_from_tail(self):
        assert tp._extract_dest("挪到文档") == "Documents"

    def test_extract_dest_from_norm_only(self):
        # 目录别名出现在别处（不在移动动词后的子句里）
        assert tp._extract_dest("桌面上的东西挪走") == "Desktop"

    def test_extract_dest_archive_default(self):
        assert tp._extract_dest("挪到归档") == tp.DEFAULT_ARCHIVE_DIR

    def test_extract_dest_none(self):
        assert tp._extract_dest("挪走") is None

    def test_explicit_path_wins_over_alias(self):
        """用户写了路径就照做，不能退回别名去猜

        「挪到 C:\\Windows\\System32」若被猜成 Desktop，计划会"成功"地做错事。
        """
        dest = tp._extract_dest("先找到桌面上的合同然后再挪到 C:\\Windows\\System32")
        assert dest == "C:\\Windows\\System32"

    def test_explicit_path_forms(self):
        assert tp._explicit_path_after_move("挪到 /tmp/x") == "/tmp/x"
        assert tp._explicit_path_after_move("挪到 ~/docs") == "~/docs"
        assert tp._explicit_path_after_move("挪到 \\\\server\\share") == "\\\\server\\share"

    def test_explicit_path_requires_move_verb(self):
        assert tp._explicit_path_after_move("C:\\Windows 里的文件") is None

    def test_explicit_path_absent_falls_back(self):
        assert tp._explicit_path_after_move("挪到归档") is None
        assert tp._extract_dest("挪到归档") == tp.DEFAULT_ARCHIVE_DIR

    def test_verb_end(self):
        assert tp._verb_end("先找再挪到文档", tp.MOVE_VERBS) == 5
        assert tp._verb_end("没有动词", tp.MOVE_VERBS) == -1

    def test_verb_end_prefers_longest_at_same_position(self):
        """"找一下" 与 "找" 都在下标 0 → 必须吃掉「找一下」整体，不能只吃「找」"""
        assert tp._verb_end("找一下合同", tp.SEARCH_VERBS) == 3
        assert tp._verb_end("找合同", tp.SEARCH_VERBS) == 1
        # 更早的短动词优先于更晚的长动词
        assert tp._verb_end("找找一下", tp.SEARCH_VERBS) == 2

    def test_extract_lang(self):
        assert tp._extract_lang("翻译成英文") == "en"
        assert tp._extract_lang("翻译一下") is None


class TestTemplatePlannerRecipes:

    @pytest.fixture
    def planner(self):
        return TemplatePlanner(available_actions=ALL_ACTIONS)

    def test_search_move(self, planner):
        plan = planner.plan("先找到合同然后再挪到归档")
        assert plan is not None and plan.steps[0].action == "file_search"
        assert plan.steps[1].action == "file_move"
        assert plan.steps[1].params["source"] == "${s1.paths.0}"
        assert plan.steps[1].params["dest"] == "Documents"
        assert plan.steps[1].depends_on == ["s1"]
        assert plan.source == "template"

    def test_search_delete_batch(self, planner):
        """file_delete 收列表 → 整批交出（与 file_move 收单值形成对比）"""
        plan = planner.plan("找到下载目录里的压缩包然后删掉")
        assert plan.steps[1].action == "file_delete"
        assert plan.steps[1].params["targets"] == "${s1.paths}"
        assert plan.steps[0].params == {"pattern": "*.zip", "dirs": ["Downloads"]}

    def test_search_delete_with_time_range(self, planner):
        plan = planner.plan("找一下昨天的截图然后清理")
        assert plan.steps[0].params.get("time_range") == "yesterday"

    def test_search_read(self, planner):
        plan = planner.plan("找一下桌面上的合同文件然后打开")
        assert plan.steps[1].action == "file_read"
        assert plan.steps[1].params["target"] == "${s1.paths.0}"

    def test_read_translate(self, planner):
        plan = planner.plan("读一下桌面上的合同然后翻译成英文")
        assert plan.steps[0].action == "file_read"
        assert plan.steps[1].action == "translate"
        assert plan.steps[1].params == {"text": "${s1.content}", "target_lang": "en"}

    def test_read_translate_without_lang(self, planner):
        plan = planner.plan("读一下桌面上的合同然后翻译")
        assert "target_lang" not in plan.steps[1].params

    def test_screenshot_open(self, planner):
        plan = planner.plan("截个图然后打开")
        assert plan.steps[0].action == "screenshot"
        assert plan.steps[1].params["target"] == "${s1.path}"

    def test_priority_delete_before_move(self):
        """"清理"类说法同时含挪字时应优先命中删除配方"""
        planner = TemplatePlanner(available_actions=ALL_ACTIONS)
        plan = planner.plan("找到压缩包然后挪走不要的并清理掉")
        assert plan.steps[1].action == "file_delete"

    # ── 负例 ──

    @pytest.mark.parametrize("text", [
        "", "   ", "你好呀", "帮我算一下 1+1", "找一下桌面上的合同文件",
        "把合同挪到归档再找一下",       # 动作顺序相反，无对应配方
        "找到然后删掉",                 # 抽不出文件名模式
        "找一下压缩包然后挪到",          # 无目标目录
        "找到压缩包然后删掉但是",        # 正常命中，此处仅占位
    ])
    def test_no_plan(self, planner, text):
        plan = planner.plan(text)
        if text == "找到压缩包然后删掉但是":
            assert plan is not None
        else:
            assert plan is None

    def test_recipe_disabled_when_tool_missing(self):
        """缺工具 → 相关配方自动停用（不产出跑不通的计划）"""
        p = TemplatePlanner(available_actions=["file_search"])
        assert p.plan("先找到合同然后再挪到归档") is None

    def test_available_from_context_overrides(self):
        """上下文里的工具表优先于构造时的快照"""
        p = TemplatePlanner()          # 未注入工具表 → 宽松
        plan = p.plan("先找到合同然后再挪到归档", {"available_actions": ["file_search"]})
        assert plan is None
        plan = p.plan("先找到合同然后再挪到归档",
                      {"available_actions": ["file_search", "file_move"]})
        assert plan is not None

    def test_single_step_build_is_ignored(self):
        """配方只产出单步 → 不算多步，返回 None"""
        recipe = tp.Recipe(
            recipe_id="one", name="单步", description="", requires=(),
            matches=lambda n: True,
            build=lambda n, r: [PlanStep(action="file_search", params={})],
        )
        p = TemplatePlanner(recipes=[recipe])
        assert p.plan("任意") is None

    def test_build_returning_none_is_skipped(self):
        recipe = tp.Recipe(
            recipe_id="none", name="空", description="", requires=(),
            matches=lambda n: True, build=lambda n, r: None,
        )
        assert TemplatePlanner(recipes=[recipe]).plan("任意") is None

    def test_custom_recipes_replace_defaults(self):
        recipe = tp.Recipe(
            recipe_id="two", name="两步", description="", requires=(),
            matches=lambda n: True,
            build=lambda n, r: [PlanStep("a"), PlanStep("b")],
        )
        plan = TemplatePlanner(recipes=[recipe]).plan("任意")
        assert plan is not None and plan.source == "template"

    def test_max_steps_truncates(self):
        recipe = tp.Recipe(
            recipe_id="many", name="多步", description="", requires=(),
            matches=lambda n: True,
            build=lambda n, r: [PlanStep("a")] * 9,
        )
        assert len(TemplatePlanner(recipes=[recipe], max_steps=3).plan("x").steps) == 3

    def test_build_exception_is_swallowed(self):
        def boom(n, r):
            raise RuntimeError("build 炸了")

        recipe = tp.Recipe(
            recipe_id="boom", name="炸", description="", requires=(),
            matches=lambda n: True, build=boom,
        )
        assert TemplatePlanner(recipes=[recipe]).plan("任意") is None

    def test_plan_impl_exception_is_swallowed(self):
        """_plan_impl 内部异常由 plan() 兜住，不向上抛"""
        p = TemplatePlanner()
        p._normalize = None          # 故意弄坏
        assert p.plan("先找到合同然后再挪到归档") is None

    def test_can_plan(self, planner):
        assert planner.can_plan("先找到合同然后再挪到归档")
        assert planner.can_plan("找到压缩包然后删掉")
        assert not planner.can_plan("你好呀")
        assert not planner.can_plan("")
        assert not planner.can_plan("找一下合同")

    def test_can_plan_respects_recipe_availability(self):
        p = TemplatePlanner(available_actions=["file_search"])
        assert not p.can_plan("先找到合同然后再挪到归档")

    def test_describe_recipes(self, planner):
        recipes = planner.describe_recipes()
        assert len(recipes) == len(tp.DEFAULT_RECIPES)
        assert all({"recipe_id", "name", "description", "requires"} <= set(r)
                   for r in recipes)

    def test_repr_fields(self, planner):
        assert planner.provider_name == "template"
        assert planner.capability_name == "planner"


# ══════════════════════════════════════════════════
#  2b. LLMPlanner
# ══════════════════════════════════════════════════

SCHEMAS = [
    {"type": "function", "function": {
        "name": "file_search", "description": "搜索文件",
        "parameters": {"type": "object", "properties": {"pattern": {}, "dirs": {}}}}},
    {"function": {"name": "file_move", "description": "移动"}},          # 无 type 包裹
    {"name": "file_read", "description": "读", "parameters": {}},        # 裸 function
    {"function": {"description": "没有名字"}},                            # 跳过
    "not-a-dict",                                                        # 跳过
]


def _llm(response):
    return lambda prompt: response


class TestLLMPlanner:

    def _planner(self, response, **kw):
        kw.setdefault("available_actions", ["file_search", "file_move", "file_read"])
        kw.setdefault("tool_schemas", SCHEMAS)
        kw.setdefault("recipes", [{"recipe_id": "r", "name": "搜索后归档",
                                   "description": "先搜再移", "requires": ["file_search",
                                                                       "file_move"]}])
        return LLMPlanner(llm_call=_llm(response), **kw)

    def test_valid_json(self):
        plan = self._planner(
            '{"steps":[{"action":"file_search","params":{"pattern":"*a*"},'
            '"description":"先找"},{"action":"file_move","params":{}}]}'
        ).plan("先找再挪")
        assert plan is not None
        assert [s.action for s in plan.steps] == ["file_search", "file_move"]
        assert plan.steps[0].description == "先找"
        assert plan.source == "llm"

    def test_code_fence(self):
        plan = self._planner(
            '```json\n{"steps":[{"action":"file_search","params":{}},'
            '{"action":"file_move","params":{}}]}\n```'
        ).plan("先找再挪")
        assert plan is not None

    def test_chatter_around_json(self):
        plan = self._planner(
            '好的，这是计划：{"steps":[{"action":"file_search","params":{}},'
            '{"action":"file_move","params":{}}]} 希望有帮助！'
        ).plan("先找再挪")
        assert plan is not None

    def test_braces_inside_param_value(self):
        """参数值里带花括号时，大括号扫描必须靠字符串状态而不是数括号"""
        plan = self._planner(
            '{"steps":[{"action":"file_search","params":{"pattern":"*.{zip,tar}"}},'
            '{"action":"file_move","params":{}}]}'
        ).plan("先找再挪")
        assert plan is not None

    def test_escaped_quote_inside_string(self):
        plan = self._planner(
            '{"steps":[{"action":"file_search","params":{"pattern":"a\\"b"}},'
            '{"action":"file_move","params":{}}]}'
        ).plan("先找再挪")
        assert plan is not None

    def test_escaped_quote_reaches_brace_scanner(self):
        """前置废话迫使走大括号扫描路径 —— 扫描器必须正确处理转义引号

        直接 `json.loads` 能成功时扫描器根本不会被调用，所以此处必须加前缀。
        """
        plan = self._planner(
            '好的，计划如下：{"steps":[{"action":"file_search",'
            '"params":{"pattern":"a\\"b"}},{"action":"file_move","params":{}}]}'
        ).plan("先找再挪")
        assert plan is not None

    def test_validated_steps_rejects_non_dict(self):
        """`_validated_steps` 的防御分支（plan() 路径上到不了，直接调用覆盖）"""
        p = self._planner("{}")
        assert p._validated_steps([1, 2], ["file_search"]) == []
        assert p._validated_steps("nope", []) == []

    def test_fabricated_action_dropped_but_keeps_valid(self):
        """编造的工具名只丢那一步，不整单放弃"""
        plan = self._planner(
            '{"steps":[{"action":"file_search","params":{}},'
            '{"action":"hack_nasa","params":{}},{"action":"file_move","params":{}}]}'
        ).plan("先找再挪")
        assert [s.action for s in plan.steps] == ["file_search", "file_move"]

    def test_all_fabricated_returns_none(self):
        assert self._planner(
            '{"steps":[{"action":"a","params":{}},{"action":"b","params":{}}]}'
        ).plan("x") is None

    def test_depends_on_normalized(self):
        plan = self._planner(
            '{"steps":[{"action":"file_search","params":{}},'
            '{"action":"file_move","params":{},"depends_on":["s1",3,null]}]}'
        ).plan("x")
        assert plan.steps[1].depends_on == ["s1", "3"]

    @pytest.mark.parametrize("response", [
        None, "", "   ", "我建议你先搜索再移动", "[1,2,3]", '{"steps":"nope"}',
        '{"steps":[]}', '{"steps":[{"action":"file_search","params":{}}]}',
        "{{{{", "}{",
    ])
    def test_unusable_responses(self, response):
        assert self._planner(response).plan("先找再挪") is None

    def test_params_non_dict_coerced(self):
        plan = self._planner(
            '{"steps":[{"action":"file_search","params":"oops"},'
            '{"action":"file_move","params":null}]}'
        ).plan("x")
        assert plan.steps[0].params == {} and plan.steps[1].params == {}

    def test_non_dict_step_entries_dropped(self):
        plan = self._planner(
            '{"steps":["junk",null,{"action":"file_search","params":{}},'
            '{"action":"file_move","params":{}}]}'
        ).plan("x")
        assert [s.action for s in plan.steps] == ["file_search", "file_move"]

    def test_truncates_to_max_steps(self):
        big = '{"steps":[' + ",".join(
            '{"action":"file_search","params":{}}' for _ in range(10)) + ']}'
        assert len(self._planner(big, max_steps=4).plan("x").steps) == 4

    def test_no_llm_call(self):
        assert LLMPlanner().plan("x") is None

    def test_empty_goal(self):
        assert self._planner("{}").plan("") is None
        assert self._planner("{}").plan(None) is None

    def test_llm_exception_swallowed(self):
        def boom(prompt):
            raise RuntimeError("LLM 炸了")

        p = LLMPlanner(llm_call=boom, available_actions=["file_search"])
        assert p.plan("x") is None

    def test_llm_call_is_bounded_by_timeout(self):
        """LLM 调用必须有上限：实测一次失败调用耗时 42s，而规划跑在调用线程
        （语音回调 = UI 线程）里，会把界面冻住"""
        import time as _t

        def slow(prompt):
            _t.sleep(2.0)
            return '{"steps":[{"action":"a","params":{}},{"action":"b","params":{}}]}'

        p = LLMPlanner(llm_call=slow, available_actions=["a", "b"], llm_timeout=0.2)
        t0 = _t.perf_counter()
        assert p.plan("先找再挪") is None
        assert (_t.perf_counter() - t0) < 1.0, "超时后应立即放弃，而不是等 LLM 返回"

    def test_llm_call_within_timeout_succeeds(self):
        p = LLMPlanner(
            llm_call=_llm('{"steps":[{"action":"a","params":{}},'
                          '{"action":"b","params":{}}]}'),
            available_actions=["a", "b"], llm_timeout=5.0,
        )
        assert p.plan("先找再挪") is not None

    def test_llm_timeout_floor(self):
        """超时值被收敛到正数（0/负数不会变成"永不超时"或立刻放弃）"""
        assert LLMPlanner(llm_timeout=0)._llm_timeout == 0.1
        assert LLMPlanner(llm_timeout=-5)._llm_timeout == 0.1

    def test_call_bounded_returns_raw_on_success(self):
        p = LLMPlanner(llm_call=_llm("OK"), available_actions=["a"])
        assert p._call_bounded("prompt") == "OK"

    def test_call_bounded_swallows_exception(self):
        def boom(prompt):
            raise RuntimeError("炸")

        p = LLMPlanner(llm_call=boom, available_actions=["a"])
        assert p._call_bounded("prompt") is None

    def test_unknown_available_is_permissive(self):
        """未注入工具表 → 放行（由管线的真实注册表兜底）"""
        plan = LLMPlanner(llm_call=_llm(
            '{"steps":[{"action":"whatever","params":{}},'
            '{"action":"also_unknown","params":{}}]}')).plan("x")
        assert plan is not None

    # ── 提示词 ──

    def test_prompt_contains_tools_recipes_and_goal(self):
        p = self._planner("{}")
        prompt = p._build_prompt("先找再挪", {})
        assert "file_search" in prompt and "file_move" in prompt
        assert "搜索后归档" in prompt
        assert "先找再挪" in prompt
        assert "无参数" in prompt          # file_read 的 parameters 为空

    def test_format_tools_fallback_to_names(self):
        p = LLMPlanner(llm_call=_llm("{}"), available_actions=["a", "b"])
        assert p._format_tools({}) == "- a\n- b"

    def test_format_tools_no_info(self):
        assert LLMPlanner()._format_tools({}) == "（无可用工具信息）"

    def test_format_tools_respects_limit(self):
        p = LLMPlanner(tool_schemas=SCHEMAS, max_tools_in_prompt=1)
        assert p._format_tools({}).count("\n") == 0

    def test_format_tools_context_wins(self):
        p = LLMPlanner(tool_schemas=SCHEMAS)
        got = p._format_tools({"tool_schemas": [
            {"function": {"name": "only", "description": "d"}}]})
        assert got == "- only（无参数） — d"

    def test_format_dirs_uses_real_aliases(self):
        """目录词表来自管线注入的真实白名单路径，别名与 file_tools 匹配用的键一致"""
        got = LLMPlanner()._format_dirs(
            {"dir_aliases": {"Downloads": r"C:\Users\x\Downloads"}})
        assert "- Downloads = C:\\Users\\x\\Downloads" in got
        assert "不要自己编造" in got

    def test_format_dirs_alias_without_path(self):
        assert LLMPlanner()._format_dirs({"dir_aliases": {"Desktop": ""}}) \
            == "\n可用目录（路径参数请用这些别名或路径，不要自己编造）：\n- Desktop\n"

    def test_format_dirs_skips_blank_alias(self):
        got = LLMPlanner()._format_dirs({"dir_aliases": {"": "/x"}})
        assert got.startswith("\n可用目录：未提供")

    def test_format_dirs_fallback_when_absent_or_not_a_dict(self):
        """没有词表（或调用方给了个列表）时退回兜底文案，仍禁止编造绝对路径"""
        assert LLMPlanner()._format_dirs({}).startswith("\n可用目录：未提供")
        assert LLMPlanner()._format_dirs({"dir_aliases": ["Desktop"]}) \
            .startswith("\n可用目录：未提供")

    def test_prompt_forbids_inventing_paths(self):
        """真实 LLM 曾编出 C:/Users/Username/Downloads —— 提示词必须堵住这条路"""
        prompt = LLMPlanner()._build_prompt("整理下载目录", {})
        assert "绝对不要自己编造路径" in prompt
        assert "C:/Users/Someone/Downloads" in prompt

    def test_prompt_tells_single_vs_list_placeholder(self):
        """实测 LLM 把列表占位符喂给单值的 file_move.source"""
        prompt = LLMPlanner()._build_prompt("先找再挪", {})
        assert "只能**喂给收列表的参数" in prompt or "只能**喂给收列表的参数，如 file_delete.targets" in prompt
        assert "${s1.paths.0}" in prompt and "file_move.source" in prompt

    def test_format_recipes_edge_cases(self):
        assert LLMPlanner()._format_recipes() == "（无）"
        p = LLMPlanner(recipes=[{"recipe_id": "r1"}, "junk", {"name": "n"}])
        assert "r1" in p._format_recipes()

    def test_describe_recipes_returns_injected(self):
        p = LLMPlanner(recipes=[{"recipe_id": "r"}])
        assert p.describe_recipes() == [{"recipe_id": "r"}]

    # ── can_plan ──

    @pytest.mark.parametrize("text,expected", [
        ("先找到合同然后再挪到归档", True),
        ("分三步整理一下", True),
        ("分 3 步做", True),
        ("你好呀", False),
        ("找一下合同", False),
        ("最后找一下合同", False),      # 连接词左侧为空
        ("再找找合同", False),          # 右侧不足
        ("帮我看看", False),
        ("", False),
    ])
    def test_can_plan(self, text, expected):
        assert LLMPlanner().can_plan(text) == expected


class TestJSONScanning:

    def test_scan_object_no_brace(self):
        assert tp is not None
        from agent.providers.planner.llm_planner import _scan_object
        assert _scan_object("no braces here") is None

    def test_scan_object_unbalanced(self):
        from agent.providers.planner.llm_planner import _scan_object
        assert _scan_object('{"a": 1') is None

    def test_json_candidates_fence_empty(self):
        from agent.providers.planner.llm_planner import _loads_lenient
        assert _loads_lenient("```json\n```") is None

    def test_loads_lenient_empty(self):
        from agent.providers.planner.llm_planner import _loads_lenient
        assert _loads_lenient("") is None
        assert _loads_lenient(None) is None

    def test_loads_lenient_non_object_json(self):
        from agent.providers.planner.llm_planner import _loads_lenient
        assert _loads_lenient("[1,2]") is None

    def test_param_names_non_dict(self):
        from agent.providers.planner.llm_planner import _param_names
        assert _param_names(None) == []
        assert _param_names({"properties": "x"}) == []


# ══════════════════════════════════════════════════
#  2c. HybridPlanner
# ══════════════════════════════════════════════════

class TestHybridPlanner:

    def test_rule_hit_does_not_call_llm(self):
        calls = []

        def spy(prompt):
            calls.append(prompt)
            return "{}"

        h = HybridPlanner(
            rule=TemplatePlanner(available_actions=ALL_ACTIONS),
            llm=LLMPlanner(llm_call=spy),
        )
        plan = h.plan("先找到合同然后再挪到归档")
        assert plan.source == "template" and calls == []
        assert h.stats()["by_rule"] == 1

    def test_llm_fallback(self):
        llm = LLMPlanner(llm_call=_llm(
            '{"steps":[{"action":"file_search","params":{}},'
            '{"action":"file_move","params":{}}]}'),
            available_actions=["file_search", "file_move"])
        h = HybridPlanner(rule=TemplatePlanner(available_actions=ALL_ACTIONS), llm=llm)
        plan = h.plan("把下载目录收拾一下然后归档")
        assert plan.source == "llm"
        assert h.stats()["by_llm"] == 1 and h.stats()["llm_attempts"] == 1

    def test_llm_skipped_when_not_multi_step(self):
        llm = LLMPlanner(llm_call=_llm('{"steps":[]}'))
        h = HybridPlanner(rule=TemplatePlanner(available_actions=ALL_ACTIONS), llm=llm)
        assert h.plan("你好呀") is None
        assert h.stats()["llm_skipped"] == 1 and h.stats()["llm_attempts"] == 0

    def test_hint_can_be_disabled(self):
        llm = LLMPlanner(llm_call=_llm('{"steps":[]}'))
        h = HybridPlanner(rule=None, llm=llm, require_multi_step_hint=False)
        h.plan("你好呀")
        assert h.stats()["llm_attempts"] == 1

    def test_rule_exception_falls_through_to_llm(self):
        class BadRule(TemplatePlanner):
            def plan(self, text, context=None):
                raise RuntimeError("规则炸了")

        llm = LLMPlanner(llm_call=_llm(
            '{"steps":[{"action":"file_search","params":{}},'
            '{"action":"file_move","params":{}}]}'),
            available_actions=["file_search", "file_move"])
        h = HybridPlanner(rule=BadRule(), llm=llm)
        assert h.plan("先找然后再挪").source == "llm"

    def test_llm_exception_swallowed(self):
        def boom(prompt):
            raise RuntimeError("LLM 炸了")

        h = HybridPlanner(rule=None, llm=LLMPlanner(llm_call=boom,
                                                    available_actions=["a"]))
        assert h.plan("先找然后再挪") is None

    def test_llm_plan_raising_is_caught_by_hybrid(self):
        """LLMPlanner 自己兜了异常；此用例覆盖"规划器实现直接抛异常"的兜底"""
        class RaisingLLM(PlannerService):
            capability_name = "planner"

            def plan(self, text, context=None):
                raise RuntimeError("实现炸了")

            def can_plan(self, text):
                return True

        h = HybridPlanner(rule=None, llm=RaisingLLM())
        assert h.plan("先找然后再挪") is None
        assert h.stats()["llm_attempts"] == 1 and h.stats()["by_llm"] == 0

    def test_can_plan_exception_treated_as_false(self):
        class BadLLM(LLMPlanner):
            def can_plan(self, text):
                raise RuntimeError("预判炸了")

        h = HybridPlanner(rule=None, llm=BadLLM(llm_call=_llm("{}")))
        assert h.plan("先找然后再挪") is None
        assert h.stats()["llm_skipped"] == 1

    def test_disabled(self):
        h = HybridPlanner(rule=TemplatePlanner(available_actions=ALL_ACTIONS))
        h.set_enabled(False)
        assert h.plan("先找到合同然后再挪到归档") is None
        assert h.can_plan("先找到合同然后再挪到归档") is False
        assert h.stats()["enabled"] is False

    def test_no_rule_no_llm(self):
        h = HybridPlanner()
        assert h.plan("x") is None
        assert h.can_plan("x") is False
        assert h.describe_recipes() == []
        assert "HybridPlanner" in repr(h)

    def test_can_plan_delegates(self):
        rule = TemplatePlanner(available_actions=ALL_ACTIONS)
        h = HybridPlanner(rule=rule)
        assert h.can_plan("先找到合同然后再挪到归档")
        assert not h.can_plan("你好呀")
        assert len(h.describe_recipes()) > 0

    def test_can_plan_via_llm_hint(self):
        h = HybridPlanner(rule=TemplatePlanner(available_actions=["file_search"]),
                          llm=LLMPlanner(llm_call=_llm("{}")))
        # 规则因缺工具不认，但 LLM 的文本预判认
        assert h.can_plan("先找到合同然后再挪到归档")

    def test_reset_stats(self):
        h = HybridPlanner(rule=TemplatePlanner(available_actions=ALL_ACTIONS))
        h.plan("先找到合同然后再挪到归档")
        assert h.stats()["by_rule"] == 1
        h.reset_stats()
        assert h.stats()["by_rule"] == 0


# ══════════════════════════════════════════════════
#  3. 规则路由的 plan 意图
# ══════════════════════════════════════════════════

class TestRouterPlanIntent:

    @pytest.fixture
    def router(self):
        return RuleRouter()

    @pytest.mark.parametrize("text", [
        "先找到合同然后再挪到归档",
        "分三步把下载目录整理一下",
        "分 3 步做",
        "先看看电脑状态再打开笔记",
        "找一下合同并且把它打开",
    ])
    def test_routes_to_plan(self, router, text):
        cmd = router.route(text)
        assert cmd.action == "plan"
        assert cmd.params["goal"] == text
        assert cmd.confidence >= 0.78

    @pytest.mark.parametrize("text,expected", [
        ("先看看电脑状态", "system_info"),      # 「先」+ 单动作 ≠ 多步
        ("看看电脑上的笔记", "file_read"),
        ("再找找桌面上的合同", "file_search"),
        ("最后找一下合同", "file_search"),       # 连接词左侧为空
        ("找一下桌面上的合同文件", "file_search"),
        ("删除桌面上的截图", "file_delete"),
        ("帮我算一下 1+1", "calculate"),
        ("你好呀", "chat"),
        ("打开 http://example.com", "web_open"),
    ])
    def test_does_not_hijack_single_step(self, router, text, expected):
        assert router.route(text).action == expected

    def test_empty_text(self, router):
        assert router.route("").action == "chat"
        assert router.route(None).action == "chat"

    def test_plan_intent_is_advertised_to_llm(self, router):
        """plan 本身不参与打分，但必须出现在意图描述里告知 LLM 路由"""
        actions = [d["action"] for d in router.describe_intents()]
        assert "plan" in actions
        assert "plan" in router.supported_actions()

    def test_plan_intent_has_no_keywords(self, router):
        intent = router._action_map["plan"]
        assert intent.keywords == []
        assert intent.keyword_weight("然后") == 0.0

    def test_distinct_intent_hits(self, router):
        hits = router._distinct_intent_hits("找一下合同然后删掉")
        assert "file_search" in hits and "file_delete" in hits
        assert "plan" not in hits

    def test_has_plan_connector(self, router):
        assert router._has_plan_connector("找合同然后再删掉")
        assert not router._has_plan_connector("最后找一下合同")
        assert not router._has_plan_connector("")

    def test_extract_plan_params_empty(self, router):
        assert router._extract_plan_params("x", "   ", {}) == {}
        assert router._extract_plan_params("x", "有内容", {}) == {"goal": "有内容"}

    def test_multi_step_precheck_empty_text(self, router):
        """`route()` 已对空文本早退，故这条防御分支要直接调用才覆盖得到"""
        assert router._route_multi_step("", "", {}) is None
        assert router._route_multi_step("   ", "", {}) is None

    def test_count_marker_wins_over_missing_intents(self, router):
        """计数式标记不要求命中任何意图 —— 交给 LLM 规划器去认"""
        assert router.route("分两步走是什么意思").action == "plan"


# ══════════════════════════════════════════════════
#  4. planner 插件装配
# ══════════════════════════════════════════════════

class TestPlannerPlugin:

    def _ctx(self, **cfg_over):
        cfg = type("Cfg", (), {
            "planner_enabled": True, "planner_provider": "hybrid",
            "planner_max_steps": 6, **cfg_over,
        })()
        ctx = Context()
        ctx.provide(SVC_CONFIG, cfg)
        return ctx

    def test_hybrid_default(self):
        ctx = self._ctx()
        assert planner_plugin_setup(ctx) is None
        assert isinstance(ctx.use(SVC_PLANNER), HybridPlanner)

    def test_template_provider(self):
        ctx = self._ctx(planner_provider="template")
        planner_plugin_setup(ctx)
        assert isinstance(ctx.use(SVC_PLANNER), TemplatePlanner)

    def test_llm_provider_without_llm_degrades_to_rule(self):
        ctx = self._ctx(planner_provider="llm")
        planner_plugin_setup(ctx)
        assert isinstance(ctx.use(SVC_PLANNER), TemplatePlanner)

    def test_llm_provider_with_llm(self):
        ctx = self._ctx(planner_provider="llm")
        ctx.provide(SVC_LLM_ONCE, _llm("{}"))
        planner_plugin_setup(ctx)
        assert isinstance(ctx.use(SVC_PLANNER), LLMPlanner)

    def test_hybrid_with_llm_gets_recipes_as_hint(self):
        ctx = self._ctx()
        ctx.provide(SVC_LLM_ONCE, _llm("{}"))
        planner_plugin_setup(ctx)
        planner = ctx.use(SVC_PLANNER)
        assert isinstance(planner, HybridPlanner)
        assert planner._llm.describe_recipes()      # 配方已注入作提示

    def test_unknown_provider_falls_back_to_hybrid(self):
        ctx = self._ctx(planner_provider="nonsense")
        planner_plugin_setup(ctx)
        assert isinstance(ctx.use(SVC_PLANNER), HybridPlanner)

    def test_disabled_provides_nothing(self):
        ctx = self._ctx(planner_enabled=False)
        planner_plugin_setup(ctx)
        assert not ctx.has(SVC_PLANNER)

    def test_max_steps_from_config(self):
        ctx = self._ctx(planner_provider="template", planner_max_steps=2)
        planner_plugin_setup(ctx)
        planner = ctx.use(SVC_PLANNER)
        plan = planner.plan("先找到合同然后再挪到归档",
                            {"available_actions": ALL_ACTIONS})
        assert len(plan.steps) <= 2


# ══════════════════════════════════════════════════
#  5. 管线多步执行（真实执行器 + 真实守卫 + 假工具）
# ══════════════════════════════════════════════════

CALLS: list = []

#: 假工具返回的文件路径必须落在白名单内。
#: 初版硬编码 `/tmp/a.zip`，被真实安全守卫（正确地）判定为白名单外路径，
#: 于是"第 2 步被拒绝 → 计划中止"，一连串用例以看不懂的方式失败。
#: 由 harness fixture 写入 tmp_path，保证路径真的可操作。
_SANDBOX: dict = {"base": Path("/tmp")}

#: 可靠的"多步"测试语句（必须真的能被规则路由判为 plan）
MULTI_MOVE = "先找到压缩包然后再挪到归档"     # 命中 file_search + file_move
MULTI_DELETE = "先找到压缩包然后再删掉"       # 命中 file_search + file_delete
MULTI_READ = "先找到压缩包然后再打开"         # 命中 file_search + file_read
MULTI_COUNT = "分两步找一下压缩包"            # 计数式标记，不要求命中意图


class FakeSearch(BaseTool):
    name = "file_search"
    description = "搜索文件"
    risk_level = "low"
    params_schema = {"type": "object", "properties": {"pattern": {"type": "string"}}}

    def execute(self, params):
        CALLS.append(("file_search", dict(params)))
        base = _SANDBOX["base"]
        return ToolResult.ok(
            data=[{"name": "a.zip", "path": str(base / "a.zip")},
                  {"name": "b.zip", "path": str(base / "b.zip")}],
            summary="找到 2 个文件", count=2,
        )


class FakeMove(BaseTool):
    name = "file_move"
    description = "移动文件"
    risk_level = "medium"                     # → 需确认

    def execute(self, params):
        CALLS.append(("file_move", dict(params)))
        return ToolResult.ok(data={"to": params.get("dest")}, summary="已经移过去啦")

    def preview(self, params):
        return f"要把 {params.get('source')} 挪到 {params.get('dest')}"


class FakeDelete(BaseTool):
    name = "file_delete"
    description = "删除文件"
    risk_level = "high"

    def execute(self, params):
        CALLS.append(("file_delete", dict(params)))
        targets = params.get("targets") or []
        return ToolResult.ok(data={"deleted": targets},
                             summary=f"删掉 {len(targets)} 个", count=len(targets))

    def preview(self, params):
        return f"要删 {len(params.get('targets') or [])} 个文件"


class FakeLowDelete(FakeDelete):
    """删除工具的替身（风险等级由守卫的风险表决定，不由此处声明）"""
    risk_level = "low"


class FakeRead(BaseTool):
    name = "file_read"
    description = "读文件"
    risk_level = "low"
    params_schema = {"type": "object", "properties": {"target": {"type": "string"}}}

    def execute(self, params):
        CALLS.append(("file_read", dict(params)))
        return ToolResult.ok(data={"content": "正文"}, summary="读好啦")


class _Planner(PlannerService):
    """按给定步骤产出计划的测试规划器"""

    capability_name = "planner"

    def __init__(self, steps, source="test"):
        self._steps = steps
        self._source = source

    def plan(self, text, context=None):
        return Plan(goal=text, steps=list(self._steps), source=self._source)

    def can_plan(self, text):
        return True


@pytest.fixture
def harness(tmp_path):
    """真实执行器 + 真实守卫 + 假工具的管线夹具

    `BasicGuard.remember_choices` 必须为 False：否则用户批准一次删除后，
    同目录的后续删除会免确认，让"两次确认各自生效"的用例失去意义。
    """
    CALLS.clear()
    _SANDBOX["base"] = tmp_path

    def _make(planner=None, tools=None, ack=None, summarizer=None, guard=None):
        reg = ToolRegistry()
        for t in (tools or (FakeSearch(), FakeMove(), FakeDelete())):
            reg.register(t)
        guard = guard or BasicGuard(
            whitelist=[str(tmp_path)], audit_db=None, audit_enabled=False,
            remember_choices=False,
        )
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=10)
        bus = EventBus()
        evts: list = []
        bus.on_any(lambda e: evts.append((e.type, dict(e.data))))
        pipe = AgentPipeline(
            bus=bus, router=RuleRouter(), safety=guard, executor=ex, registry=reg,
            ack_cache=ack, summarizer=summarizer, planner=planner,
        )
        pipe.start()
        return pipe, guard, ex, bus, evts

    yield _make

    CALLS.clear()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _has(evts, etype):
    return any(t == etype for t, _ in evts)


def _all(evts, etype):
    return [d for t, d in evts if t == etype]


def _pick(evts, etype):
    got = _all(evts, etype)
    return got[0] if got else None


TEMPLATE = TemplatePlanner(available_actions=ALL_ACTIONS)


def _two_step_planner():
    return _Planner([
        PlanStep(action="file_search", params={"pattern": "*.zip"}, step_id="s1"),
        PlanStep(action="file_move",
                 params={"source": "${s1.paths.0}", "dest": "Documents"}, step_id="s2",
                 depends_on=["s1"]),
    ])


class TestPipelinePlanExecution:

    def test_full_multistep_success_with_placeholder(self, harness):
        """两步计划：占位符从第 1 步产出解析，第 1 步不重复执行"""
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))

            # 第 1 步（low）已执行；第 2 步（medium）挂起
            assert [c[0] for c in CALLS] == ["file_search"]
            assert pipe.plan_count == 1
            question = _pick(evts, EventTypes.FEEDBACK_CONFIRM)["question"]
            assert "第 2 步" in question and "一共 2 步" in question
            assert "前面已经" in question

            pipe.handle_text("确定")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))

            assert [c[0] for c in CALLS] == ["file_search", "file_move"]
            assert CALLS[1][1]["source"] == str(_SANDBOX["base"] / "a.zip")
            assert CALLS[1][1]["dest"] == "Documents"
            fin = _pick(evts, EventTypes.PLAN_FINISHED)
            assert fin["status"] == "success"
            assert fin["done"] == 2 and fin["total"] == 2
            assert sum(1 for c in CALLS if c[0] == "file_search") == 1
            assert pipe.plan_count == 0
        finally:
            pipe.stop(); ex.shutdown()

    def test_events_and_stats(self, harness):
        pipe, guard, ex, bus, evts = harness(_Planner([
            PlanStep(action="file_search", params={}, step_id="s1"),
            PlanStep(action="file_search", params={}, step_id="s2"),
        ]))
        try:
            pipe.handle_text("分两步找东西")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))

            started = _pick(evts, EventTypes.PLAN_STARTED)
            assert [s["step_id"] for s in started["steps"]] == ["s1", "s2"]
            assert started["plan_source"] == "test"   # 不可用 source（会被 emit 吞掉）
            assert started["total"] == 2

            steps = _all(evts, EventTypes.PLAN_STEP_DONE)
            assert len(steps) == 2
            assert steps[0]["step_id"] == "s1" and steps[0]["success"] is True

            st = pipe.stats()
            assert st["plans"] == 1 and st["plan_steps"] == 2
            assert st["plan_pending"] == 0 and st["plan_failed"] == 0
        finally:
            pipe.stop(); ex.shutdown()

    def test_confirm_then_cancel_aborts_plan(self, harness):
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            pipe.handle_text("算了")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))

            assert not any(c[0] == "file_move" for c in CALLS)
            fin = _pick(evts, EventTypes.PLAN_FINISHED)
            assert fin["status"] == "cancelled"
            assert "改不了哦" in fin["summary"]
            assert pipe.stats()["plan_cancelled"] == 1
            assert pipe.plan_count == 0
        finally:
            pipe.stop(); ex.shutdown()

    def test_cancel_before_any_step_ran(self, harness):
        """首步就需确认时取消 → 文案不说"已经做完" """
        pipe, guard, ex, bus, evts = harness(_Planner([
            PlanStep(action="file_move", params={"source": "a", "dest": "b"}, step_id="s1"),
            PlanStep(action="file_search", params={}, step_id="s2"),
        ]))
        try:
            pipe.handle_text("先把它挪到文档然后再找一下")
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            assert CALLS == []
            pipe.handle_text("算了")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            assert _pick(evts, EventTypes.PLAN_FINISHED)["summary"] == "好，那就不做啦~（你取消了）"
        finally:
            pipe.stop(); ex.shutdown()

    def test_each_medium_step_needs_its_own_confirmation(self, harness):
        """批准标记只对当前步生效 —— 否则第 3 步会在未确认下执行"""
        pipe, guard, ex, bus, evts = harness(_Planner([
            PlanStep(action="file_search", params={}, step_id="s1"),
            PlanStep(action="file_move", params={"source": "a", "dest": "b"}, step_id="s2"),
            PlanStep(action="file_move", params={"source": "c", "dest": "d"}, step_id="s3"),
        ]))
        try:
            pipe.handle_text(MULTI_COUNT)
            _wait(lambda: len(_all(evts, EventTypes.FEEDBACK_CONFIRM)) == 1)
            assert _pick(evts, EventTypes.FEEDBACK_CONFIRM)["question"].startswith(
                "这是第 2 步，一共 3 步")

            pipe.handle_text("确定")
            _wait(lambda: len(_all(evts, EventTypes.FEEDBACK_CONFIRM)) == 2)
            # 第二次问的是第 3 步（而不是复用第 2 步的问题）
            assert _all(evts, EventTypes.FEEDBACK_CONFIRM)[1]["question"].startswith(
                "这是第 3 步，一共 3 步")
            # 第 2 步已执行，但第 3 步（dest=d）必须还没跑
            assert not any(c[1].get("dest") == "d" for c in CALLS)

            pipe.handle_text("确定")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            assert [c[0] for c in CALLS] == ["file_search", "file_move", "file_move"]
            assert _pick(evts, EventTypes.PLAN_FINISHED)["done"] == 3
        finally:
            pipe.stop(); ex.shutdown()

    def test_step_rejected_by_safety_aborts_plan(self, harness):
        """逐步安全校验：白名单外路径 → 中止，后续步骤一步都不执行"""
        pipe, guard, ex, bus, evts = harness(_Planner([
            PlanStep(action="file_search", params={"pattern": "*"}, step_id="s1"),
            PlanStep(action="file_move",
                     params={"source": "${s1.paths.0}", "dest": "C:\\Windows\\System32"},
                     step_id="s2", depends_on=["s1"]),
        ]))
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))

            assert not any(c[0] == "file_move" for c in CALLS)
            fin = _pick(evts, EventTypes.PLAN_FINISHED)
            assert fin["status"] == "failed" and fin["done"] == 1
            assert "改不了哦" in fin["summary"]
            assert pipe.stats()["rejected"] == 1
        finally:
            pipe.stop(); ex.shutdown()

    def test_first_step_failure(self, harness):
        class FailSearch(FakeSearch):
            def execute(self, params):
                CALLS.append(("file_search", dict(params)))
                return ToolResult.fail("搜不动")

        pipe, guard, ex, bus, evts = harness(
            _Planner([PlanStep(action="file_search", params={}, step_id="s1"),
                      PlanStep(action="file_search", params={}, step_id="s2")]),
            tools=(FailSearch(),),
        )
        try:
            pipe.handle_text(MULTI_COUNT)
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            fin = _pick(evts, EventTypes.PLAN_FINISHED)
            assert fin["status"] == "failed" and fin["done"] == 0
            assert "第 1 步就卡住了" in fin["summary"]
            assert "搜不动" in fin["summary"]
        finally:
            pipe.stop(); ex.shutdown()

    def test_midway_failure_is_partial(self, harness):
        """中途失败 → PARTIAL，且如实说明前面已做的不回滚"""
        class FailSecond(FakeSearch):
            def __init__(self):
                self._n = 0

            def execute(self, params):
                self._n += 1
                CALLS.append(("file_search", dict(params)))
                if self._n == 2:
                    return ToolResult.fail("第二步炸了")
                return ToolResult.ok(
                    data=[{"path": str(_SANDBOX["base"] / "a")}],
                    summary="第一步好了")

        pipe, guard, ex, bus, evts = harness(
            _Planner([PlanStep(action="file_search", params={}, step_id="s1"),
                      PlanStep(action="file_search", params={}, step_id="s2")]),
            tools=(FailSecond(),),
        )
        try:
            pipe.handle_text("分两步找东西")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            fin = _pick(evts, EventTypes.PLAN_FINISHED)
            assert fin["status"] == "partial"
            assert fin["done"] == 1 and fin["total"] == 2
            assert "第一步好了" in fin["summary"]
            assert "改不了哦" in fin["summary"]
            assert pipe.stats()["plan_failed"] == 1
        finally:
            pipe.stop(); ex.shutdown()

    def test_interrupt_cancels_plan_without_announcing(self, harness):
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            bus.emit(EventTypes.SPEECH_INTERRUPTED, request_id="")
            assert pipe.plan_count == 0
            assert _pick(evts, EventTypes.PLAN_FINISHED)["status"] == "cancelled"
            assert not _has(evts, EventTypes.FEEDBACK_RESULT)
        finally:
            pipe.stop(); ex.shutdown()

    def test_interrupt_with_request_id(self, harness):
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            cmd = pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            bus.emit(EventTypes.SPEECH_INTERRUPTED, request_id=cmd.request_id)
            assert pipe.plan_count == 0
        finally:
            pipe.stop(); ex.shutdown()

    def test_stop_cancels_plans(self, harness):
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            pipe.stop()
            assert pipe.plan_count == 0
            fin = _pick(evts, EventTypes.PLAN_FINISHED)
            assert fin["status"] == "cancelled" and "管线停止" in fin["summary"]
        finally:
            ex.shutdown()

    def test_no_planner_falls_back_to_chat(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            cmd = pipe.handle_text(MULTI_MOVE)
            assert cmd.action == "plan"
            assert _has(evts, "pipeline.chat")
            assert CALLS == [] and pipe.stats()["plans"] == 0
        finally:
            pipe.stop(); ex.shutdown()

    def test_single_step_plan_falls_back_to_chat(self, harness):
        pipe, guard, ex, bus, evts = harness(
            _Planner([PlanStep(action="file_search", params={})]))
        try:
            pipe.handle_text(MULTI_MOVE)
            assert _has(evts, "pipeline.chat")
            assert pipe.stats()["plans"] == 0 and CALLS == []
        finally:
            pipe.stop(); ex.shutdown()

    def test_plan_with_unknown_tool_is_rejected(self, harness):
        """规划器（尤其 LLM）可能编造工具名 → 提交前用真实注册表核对"""
        pipe, guard, ex, bus, evts = harness(_Planner([
            PlanStep(action="file_search", params={}, step_id="s1"),
            PlanStep(action="hack_nasa", params={}, step_id="s2"),
        ]))
        try:
            pipe.handle_text(MULTI_MOVE)
            assert _has(evts, "pipeline.chat")
            assert CALLS == [] and pipe.stats()["plans"] == 0
        finally:
            pipe.stop(); ex.shutdown()

    def test_planner_exception_falls_back_to_chat(self, harness):
        class Boom(PlannerService):
            capability_name = "planner"

            def plan(self, text, context=None):
                raise RuntimeError("规划器炸了")

        pipe, guard, ex, bus, evts = harness(Boom())
        try:
            pipe.handle_text(MULTI_MOVE)
            assert _has(evts, "pipeline.chat")
        finally:
            pipe.stop(); ex.shutdown()

    def test_empty_goal_falls_back(self, harness):
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            cmd = AgentCommand(action="plan", params={"goal": "  "})
            assert pipe._try_start_plan("r-empty", cmd) is False
        finally:
            pipe.stop(); ex.shutdown()

    def test_template_planner_end_to_end(self, harness):
        """用真实 TemplatePlanner 跑通"搜 → 删"完整链路（含确认）

        注意：`file_delete` 是否需要确认由 **守卫的风险表**按动作名决定，
        工具自己声明的 `risk_level` 不参与 —— 所以这里必须真的走一次确认。
        """
        pipe, guard, ex, bus, evts = harness(
            TEMPLATE, tools=(FakeSearch(), FakeDelete()))
        try:
            pipe.handle_text(MULTI_DELETE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            assert [c[0] for c in CALLS] == ["file_search"]

            pipe.handle_text("确定")
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            base = _SANDBOX["base"]
            assert [c[0] for c in CALLS] == ["file_search", "file_delete"]
            assert CALLS[1][1]["targets"] == [str(base / "a.zip"),
                                              str(base / "b.zip")]
            assert _pick(evts, EventTypes.PLAN_FINISHED)["status"] == "success"
        finally:
            pipe.stop(); ex.shutdown()

    def test_step_params_go_through_reference_resolution(self, harness):
        """步骤参数里的指代（"第一个"）也要被解析

        指代消解只认 `target` / `targets` 键（`_resolve_references` 的既有约定），
        所以这里用 `file_read(target=...)` 而不是 `file_move(source=...)`。
        """
        pipe, guard, ex, bus, evts = harness(
            _Planner([
                PlanStep(action="file_search", params={"pattern": "*"}, step_id="s1"),
                PlanStep(action="file_read", params={"target": "第一个"}, step_id="s2"),
            ]),
            tools=(FakeSearch(), FakeRead()),
        )
        try:
            base = _SANDBOX["base"]
            pipe.tracker.push_files([{"name": "x", "path": str(base / "x.zip")}])
            pipe.handle_text(MULTI_READ)
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            assert CALLS[1] == ("file_read", {"target": str(base / "x.zip")})
        finally:
            pipe.stop(); ex.shutdown()

    def test_high_risk_step_uses_double_confirm_wording(self, harness):
        """高风险步骤的确认问句用"比较重要"措辞"""
        pipe, guard, ex, bus, evts = harness(_Planner([
            PlanStep(action="file_search", params={"pattern": "*"}, step_id="s1"),
            PlanStep(action="file_delete", params={"targets": ["a"]}, step_id="s2"),
        ]))
        try:
            pipe.handle_text(MULTI_DELETE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            assert "比较重要" in _pick(evts, EventTypes.FEEDBACK_CONFIRM)["question"]
        finally:
            pipe.stop(); ex.shutdown()


class TestPipelinePlanEdgeCases:
    """直接调用内部方法覆盖结构性边界"""

    def test_aggregate_summary_empty(self, harness):
        """计划无成功步骤时的兜底文案"""
        pipe, guard, ex, bus, evts = harness(None)
        try:
            run = _PlanRun(request_id="r", plan=Plan(steps=[PlanStep("a")]))
            assert pipe._aggregate_summary(run) == "都做完啦~"
        finally:
            pipe.stop(); ex.shutdown()

    def test_on_step_done_ignores_cancelled_handle(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            run = _PlanRun(request_id="r", plan=Plan(steps=[PlanStep("a")]*2))
            run.status = PlanStatus.RUNNING
            handle = type("H", (), {"cancelled": True})()
            pipe._on_plan_step_done(run, run.plan.steps[0], handle,
                                    ToolResult.ok(summary="x"))
            assert run.results == {}
        finally:
            pipe.stop(); ex.shutdown()

    def test_on_step_done_ignores_non_running(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            run = _PlanRun(request_id="r", plan=Plan(steps=[PlanStep("a")]*2))
            run.status = PlanStatus.CANCELLED
            handle = type("H", (), {"cancelled": False})()
            pipe._on_plan_step_done(run, run.plan.steps[0], handle,
                                    ToolResult.ok(summary="x"))
            assert run.results == {}
        finally:
            pipe.stop(); ex.shutdown()

    def test_advance_plan_not_running_is_noop(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            run = _PlanRun(request_id="r", plan=Plan(steps=[PlanStep("a")]*2))
            run.status = PlanStatus.HALTED
            pipe._advance_plan(run)
            assert CALLS == []
        finally:
            pipe.stop(); ex.shutdown()

    def test_safety_check_exception_aborts(self, harness):
        class BadGuard:
            def check(self, action, params):
                raise RuntimeError("守卫炸了")

            def pending_count(self):
                return 0

            def get_pending(self, rid):
                return None

            def audit(self, *a, **k):
                pass

            def request_confirm(self, *a, **k):
                return ""

            def resolve_confirm(self, *a, **k):
                return False

        pipe, guard, ex, bus, evts = harness(_two_step_planner(), guard=BadGuard())
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.PLAN_FINISHED))
            assert _pick(evts, EventTypes.PLAN_FINISHED)["status"] == "failed"
            assert CALLS == []
        finally:
            pipe.stop(); ex.shutdown()

    def test_resume_plan_missing_or_wrong_state(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            assert pipe._resume_plan("nope") is False

            run = _PlanRun(request_id="r", plan=Plan(steps=[PlanStep("a")]*2))
            run.status = PlanStatus.RUNNING
            pipe._plans["r"] = run
            assert pipe._resume_plan("r") is False      # 未挂起

            run.status = PlanStatus.HALTED
            run.index = run.total                       # 已无剩余步骤
            assert pipe._resume_plan("r") is False
        finally:
            pipe.stop(); ex.shutdown()

    def test_cancel_plan_missing(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            assert pipe._cancel_plan("nope") is False
        finally:
            pipe.stop(); ex.shutdown()

    def test_safe_tool_schemas_failure(self, harness):
        class BadRegistry(ToolRegistry):
            def to_llm_schemas(self, names=None):
                raise RuntimeError("导不出来")

        pipe, guard, ex, bus, evts = harness(None)
        try:
            pipe._registry = BadRegistry()
            assert pipe._safe_tool_schemas() == []
        finally:
            pipe.stop(); ex.shutdown()

    def test_safe_dir_aliases_from_whitelist(self, harness):
        """目录词表的别名必须就是 file_tools 匹配目录名时用的键"""
        pipe, guard, ex, bus, evts = harness(None)
        try:
            got = pipe._safe_dir_aliases()
            assert got
            assert set(got) == {p.name for p in guard.whitelist_roots()}
            assert all(isinstance(v, str) and v for v in got.values())
        finally:
            pipe.stop(); ex.shutdown()

    def test_safe_dir_aliases_failure(self, harness):
        """安全层读不出白名单时退化为空表，而不是让规划整条链路炸掉"""
        class BadGuard:
            def whitelist_roots(self):
                raise RuntimeError("读不出来")

        pipe, guard, ex, bus, evts = harness(None)
        try:
            pipe._safety = BadGuard()
            assert pipe._safe_dir_aliases() == {}
        finally:
            pipe._safety = guard
            pipe.stop(); ex.shutdown()

    def test_plan_context_carries_dir_aliases(self, harness):
        """管线必须把本机真实白名单目录交给规划器

        不注入时真实 LLM 会自己编绝对路径（实测 `C:/Users/Username/Downloads`），
        而非法目录会被 `file_search` 静默跳过 → 计划第一步就落空。
        """
        seen: dict = {}

        class Capture(PlannerService):
            capability_name = "planner"

            def plan(self, text, context=None):
                seen.update(context or {})
                return None

        pipe, guard, ex, bus, evts = harness(Capture())
        try:
            pipe.handle_text(MULTI_MOVE)
            assert seen.get("dir_aliases")
            assert set(seen["dir_aliases"]) == {
                p.name for p in guard.whitelist_roots()}
        finally:
            pipe.stop(); ex.shutdown()

    def test_confirm_question_without_preview_or_description(self, harness):
        """无预览、无描述时的兜底文案"""
        pipe, guard, ex, bus, evts = harness(None)
        try:
            run = _PlanRun(request_id="r", plan=Plan(steps=[
                PlanStep("file_search"), PlanStep("file_search")]))
            verdict = type("V", (), {"require_double": False})()
            q = pipe._plan_confirm_question(run, run.plan.steps[0], "", verdict)
            assert "要执行file_search" in q
        finally:
            pipe.stop(); ex.shutdown()

    def test_short_summary_truncates(self, harness):
        pipe, guard, ex, bus, evts = harness(None)
        try:
            step = PlanStep("a", description="兜底描述")
            long_result = ToolResult.ok(summary="很长" * 100)
            assert len(pipe._short_summary(step, long_result)) <= 60
            # 结果无摘要时回退到步骤描述
            assert pipe._short_summary(step, ToolResult.ok(summary="")) == "兜底描述"
            # 都没有时回退到工具名
            assert pipe._short_summary(PlanStep("a"), ToolResult.ok(summary="")) == "a"
        finally:
            pipe.stop(); ex.shutdown()

    def test_plan_accessors(self, harness):
        planner = _two_step_planner()
        pipe, guard, ex, bus, evts = harness(planner)
        try:
            assert pipe.planner is planner
            pipe.set_planner(None)
            assert pipe.planner is None
            pipe.set_planner(planner)
            assert pipe.planner is planner
            assert pipe.stats()["planner"] == "_Planner"
        finally:
            pipe.stop(); ex.shutdown()

    def test_ref_dict_last_alias(self):
        run = _PlanRun(request_id="r", plan=Plan(steps=[
            PlanStep("a", step_id="s1"), PlanStep("b", step_id="s2")]))
        run.results = {"s1": [1], "s2": {"k": 2}}
        assert run.ref_dict()["last"] == {"k": 2}
        assert run.total == 2

    def test_ref_dict_empty_results(self):
        run = _PlanRun(request_id="r", plan=Plan(steps=[PlanStep("a")]))
        assert run.ref_dict() == {}


class TestPipelineSingleStepUnchanged:
    """单步指令必须完全走原路径（P3 不得改变既有行为）"""

    def test_single_step_still_works(self, harness):
        pipe, guard, ex, bus, evts = harness(TEMPLATE)
        try:
            pipe.handle_text("找一下压缩包")
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_RESULT))
            assert not _has(evts, EventTypes.PLAN_STARTED)
            assert [c[0] for c in CALLS] == ["file_search"]
        finally:
            pipe.stop(); ex.shutdown()

    def test_reset_stats_clears_plan_counters(self, harness):
        pipe, guard, ex, bus, evts = harness(_two_step_planner())
        try:
            pipe.handle_text(MULTI_MOVE)
            _wait(lambda: _has(evts, EventTypes.FEEDBACK_CONFIRM))
            assert pipe.stats()["plans"] == 1
            pipe.reset_stats()
            st = pipe.stats()
            assert st["plans"] == 0 and st["plan_steps"] == 0
        finally:
            pipe.stop(); ex.shutdown()


# ══════════════════════════════════════════════════════
#  规划期参数类型校验（P5-A2 / D17）
# ══════════════════════════════════════════════════════

#: 带**真实 type** 的 schema —— 校验要靠它，所以不能像 SCHEMAS 那样留空
TYPED_SCHEMAS = [
    {"type": "function", "function": {
        "name": "file_search", "description": "搜索",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "dirs": {"type": "array"},
            "time_range": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "file_move", "description": "移动",
        "parameters": {"type": "object", "properties": {
            "source": {"type": "string"},
            "dest": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "file_delete", "description": "删除",
        "parameters": {"type": "object", "properties": {
            "targets": {"type": "array"}}}}},
]

_NAMES = ["file_search", "file_move", "file_delete"]


class TestPlanParamValidation:
    """D17：规划期就按工具**真实 schema** 校验参数类型

    背景：原先只校验工具名，于是 LLM 给出的 `pattern: ["*.exe","*.msi"]`
    （该字段是 string）能"成功"拆成计划，一路走到**执行期**才被 `validate_params`
    拦下 —— 用户听到的是"这个指令我还没完全理解"，而规划器那边看起来一切正常。
    实测抓到过三种：`pattern` 给列表、`file_move.source` 给列表、`targets` 套一层列表。
    """

    def _p(self, response, **kw):
        kw.setdefault("available_actions", list(_NAMES))
        kw.setdefault("tool_schemas", TYPED_SCHEMAS)
        return LLMPlanner(llm_call=_llm(response), **kw)

    def _plan(self, response, **kw):
        return self._p(response, **kw).plan("先找再挪", {})

    # ── 正方向：坏参数必须被拦在规划期 ──

    def test_wrong_type_param_drops_that_step(self):
        """`pattern` 给了列表 → 该步被丢弃，其余步骤保留（编号不重排）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*"}},'
            '{"action":"file_search","params":{"pattern":["*.exe","*.msi"]}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}'
        )
        assert plan is not None, "丢掉中间一步后仍有两步，计划应当保留"
        assert [s.action for s in plan.steps] == ["file_search", "file_move"]
        assert [s.step_id for s in plan.steps] == ["s1", "s3"], (
            "step_id 必须保持**原始下标** —— 重排会让 ${s2.paths} 静默指向另一步"
        )

    def test_dropped_step_is_logged_with_param_name(self, caplog):
        """丢弃必须留痕，且要点明**哪个参数**错在哪（否则等于静默丢弃）"""
        import logging
        with caplog.at_level(logging.WARNING):
            self._plan(
                '{"steps":['
                '{"action":"file_search","params":{"pattern":"*a*"}},'
                '{"action":"file_search","params":{"pattern":["x"]}},'
                '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
                ']}'
            )
        assert "丢弃步骤 2" in caplog.text
        # 「哪个参数 + 期望什么 + 收到什么」三样都要有，否则排查时等于没说
        assert "pattern" in caplog.text
        assert "期望 string" in caplog.text, "要说明期望的类型"
        assert "收到 list" in caplog.text, "要说明实际收到的类型"

    def test_dangling_reference_also_dropped(self):
        """引用了**已被丢弃**的步骤 → 该步也丢（悬空占位符必然失败）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*"}},'
            '{"action":"file_search","params":{"pattern":["x"]}},'
            '{"action":"file_move","params":{"source":"${s2.paths}","dest":"Desktop"}}'
            ']}'
        )
        assert plan is None, (
            "s2 被丢后 s3 的 ${s2.paths} 解析不出来，会原样字符串喂给工具 —— "
            "只剩 1 步有效 → 应当判定规划失败"
        )

    # ── 反方向：不许误伤 ──

    def test_correct_plan_untouched(self):
        """类型全对的三步计划**一步都不能少**（防"修到不能干活"）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*","dirs":["Downloads"]}},'
            '{"action":"file_move","params":{"source":"${s1.paths.0}","dest":"Documents"}},'
            '{"action":"file_delete","params":{"targets":["${s1.paths}"]}}'
            ']}'
        )
        assert plan is not None
        assert [s.action for s in plan.steps] == ["file_search", "file_move", "file_delete"]
        assert [s.step_id for s in plan.steps] == ["s1", "s2", "s3"]

    def test_explicit_null_optional_still_allowed(self):
        """`{"time_range": null}` 必须仍放行 —— F 系列缺陷 4 的回归保护

        显式 JSON null 在 function calling 里是"这个参数我没填"的常见表达，
        执行期把它当"未提供"；规划期若更严，就会丢掉本来能跑通的计划。
        """
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*","time_range":null}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}'
        )
        assert plan is not None and len(plan.steps) == 2

    def test_undeclared_field_still_allowed(self):
        """未声明的字段放行（与执行期同规则；规划期更严是另一种错）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*","whatever":1}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}'
        )
        assert plan is not None and len(plan.steps) == 2

    def test_malformed_schema_spec_is_not_a_violation(self):
        """字段 schema 本身畸形（不是 dict）→ **放行**，不能反过来丢掉用户的计划

        畸形的 schema 只会来自 LLM 侧（手写工具不会把 `parameters.properties.pattern`
        写成字符串）。取向与"不知道有哪些工具就放行"一致：
        **不确定时不假装能判** —— 判错的代价是用户的操作被静默吞掉。
        """
        broken = [{"type": "function", "function": {
            "name": "file_search", "description": "搜索",
            "parameters": {"type": "object", "properties": {"pattern": "string"}}}}]
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*"}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}',
            tool_schemas=broken,
        )
        assert plan is not None and len(plan.steps) == 2

    def test_without_schemas_validation_is_skipped(self):
        """没给 schema 时**不假装能判**（与"不知道有哪些工具就放行"同一取向）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*"}},'
            '{"action":"file_search","params":{"pattern":["x"]}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}',
            tool_schemas=[],
        )
        assert plan is not None
        assert len(plan.steps) == 3, "无 schema 时不该丢步骤"

    def test_no_schemas_but_dangling_ref_still_dropped(self):
        """没 schema 时仍然能查"引用的步骤是否存在"（那不需要 schema）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"file_search","params":{"pattern":"*a*"}},'
            '{"action":"file_move","params":{"source":"${s9.paths}","dest":"Desktop"}}'
            ']}',
            tool_schemas=[],
        )
        assert plan is None, "引用了不存在的 s9 → 只剩 1 步 → 规划失败"

    # ── 判据与执行期一致（共用 describe_value_problem）──

    def test_same_rule_as_executor(self):
        """同一个坏值，规划期与执行期给出的说法必须**一致**

        判据本体是 `agent.tools.base.describe_value_problem`，两处共用。
        这条用例把"共用"钉住：若将来有人在规划器里另写一份，两边文案会先分叉。
        """
        from agent.tools.base import describe_value_problem

        spec = {"type": "array"}
        planner_msg = LLMPlanner()._param_problem({"targets": "C:\\a.txt"},
                                                  {"type": "object",
                                                   "properties": {"targets": spec}})
        direct = describe_value_problem("C:\\a.txt", spec)
        assert direct is not None
        assert direct in planner_msg

#: 判据必须挂在**真实工具 schema** 上，而不是只为测试写的小 schema。
#: 这两个工具覆盖了全项目唯一的 `integer`（`memory_recall.limit`）与
#: 唯一的 `enum`（`kind`）—— 也就是说"布尔不能当整数""取值必须在枚举内"
#: 这两条判据在规划期**只有它们能测到**。
_REAL_TOOLS = [MemoryRecallTool(), FileMoveTool(BasicGuard())]
REAL_SCHEMAS = [t.to_llm_schema() for t in _REAL_TOOLS]


class TestRealSchemaParamValidation:
    """同一套判据挂在**真实工具 schema**（取自注册表）上的效果

    与 `TestPlanParamValidation` 的区别：那组用的是为测试写的小 schema，
    这组用的是 `BaseTool.to_llm_schema()` 的**真实输出** ——
    因为"不另写一份判据"这件事，只有在真 schema 上成立才算数。
    """

    _REAL_NAMES = [t.name for t in _REAL_TOOLS]

    def _p(self, response):
        return LLMPlanner(
            llm_call=_llm(response),
            available_actions=list(self._REAL_NAMES),
            tool_schemas=REAL_SCHEMAS,
        )

    def _plan(self, response):
        """跑一次规划（`_p` 造规划器，这里统一走 `.plan()`）"""
        return self._p(response).plan("回忆一下", {})

    def test_real_schema_rejects_wrong_enum(self):
        """`kind` 不在 enum 里 → 丢该步（真实 schema 带 enum）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"memory_recall","params":{"query":"浏览器","kind":"preference"}},'
            '{"action":"memory_recall","params":{"query":"浏览器","kind":"随便编的"}}'
            ']}'
        )
        assert plan is None, "两步都被判坏 → 有效步骤不足 2 步"

    def test_real_schema_rejects_boolean_as_integer(self):
        """布尔是 int 的子类 → `limit: true` 必须被拦（否则 `int(True)` = 1 条）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"memory_recall","params":{"query":"a"}},'
            '{"action":"memory_recall","params":{"query":"b","limit":true}}'
            ']}'
        )
        assert plan is None

    def test_real_schema_rejects_limit_out_of_range(self):
        """`limit: 9999` 超 `maximum` → 丢该步

        这一条尤其值：`MemoryRecallTool._limit` 会**静默夹到上限**，
        于是"用户要 9999 条"与"用户要 20 条"在结果上完全一样。
        规划期拦下比工具静默夹更有信息量。
        """
        plan = self._plan(
            '{"steps":['
            '{"action":"memory_recall","params":{"query":"a"}},'
            '{"action":"memory_recall","params":{"query":"b","limit":9999}}'
            ']}'
        )
        assert plan is None

    def test_real_schema_allows_reasonable_limit(self):
        """反方向：`limit: 3` 是合法值 → 两步都在（防"只要带 limit 就丢"）"""
        plan = self._plan(
            '{"steps":['
            '{"action":"memory_recall","params":{"query":"a","limit":3}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}'
        )
        assert plan is not None and len(plan.steps) == 2

    def test_real_schema_omitted_optional_field_fine(self):
        """反方向：可选字段不写（最常见的情形）→ 不受影响"""
        plan = self._plan(
            '{"steps":['
            '{"action":"memory_recall","params":{"query":"a"}},'
            '{"action":"file_move","params":{"source":"a.txt","dest":"Desktop"}}'
            ']}'
        )
        assert plan is not None and len(plan.steps) == 2

    def test_tool_params_uses_registry_schemas_verbatim(self):
        """`_tool_params` 取的就是注册表 schema 的**原文**，没有中间转换

        这是"不另写一份判据"的可检查形式：只要它原样透出 `minimum`/`maximum`/`enum`，
        就没有第二套类型表在中间做有损转换。
        """
        import json as _json

        params = LLMPlanner(tool_schemas=REAL_SCHEMAS)._tool_params({})
        from_registry = {}
        for t in _REAL_TOOLS:
            from_registry[t.name] = t.to_llm_schema()["function"]["parameters"]
        assert _json.dumps(params, sort_keys=True, ensure_ascii=False) == \
            _json.dumps(from_registry, sort_keys=True, ensure_ascii=False)
        limit = params["memory_recall"]["properties"]["limit"]
        assert limit["type"] == "integer" and "maximum" in limit

    def test_tool_params_converges_both_schema_shapes(self):
        """D16 的收敛点：已包好与扁平两种形制都要能取出 `parameters`"""
        wrapped = [{"type": "function", "function": {
            "name": "file_search",
            "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}}}}]
        flat = [{"name": "file_search",
                 "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}}}]
        a = LLMPlanner(tool_schemas=wrapped)._tool_params({})
        b = LLMPlanner(tool_schemas=flat)._tool_params({})
        assert a == b
        assert set(a) == {"file_search"}
        assert "pattern" in a["file_search"]["properties"]

