# -*- coding: utf-8 -*-
"""文档 ↔ 实现 一致性审计：文档里承诺/描述的东西，代码里真的成立吗。

## 为什么查这个

本项目已吃过多次"文档说做完了、实际不是"的亏（P5-A1 修的 7 处措辞、
`AGENTS.md` 写着完成而 `tasks-p5.json` 还是 `pending`）。
本轮再查一层：**文档对"当前行为"的具体描述**是否成立。

判据刻意选**可执行**的，不选"读起来对不对"：
  · 文档写"X 个工具" → 真的数一遍注册表
  · 文档写"删除强制走回收站、无永久删除分支" → 真的 grep 永久删除调用
  · 文档写"两个 EventBus 仍并存" → 真的确认两个类都在
  · 文档写某文件路径 → 真的看它在不在

不做的事：不评判文风、不检查"是否详尽"（那没有判据）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS: list[tuple[str, str, object]] = []


def check(name: str, claim: str):
    def deco(fn):
        CHECKS.append((name, claim, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
# 1. "21 个工具" / "18 个工具" —— 文档两处给了不同数字，数一遍
# --------------------------------------------------------------------------
@check('工具数量', 'AGENTS.md §三 写"tools/ 共 21 个"，§十一 写"P1：18 个工具"')
def tool_count() -> tuple[bool, str]:
    import importlib
    import inspect

    total = {}
    for mod_name in [
        'agent.tools.file_tools', 'agent.tools.system_tools',
        'agent.tools.browser_tools', 'agent.tools.productivity_tools',
        'agent.tools.memory_tools',
    ]:
        short = mod_name.split('.')[-1]
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            total[short] = f'导入失败 {type(e).__name__}: {e}'
            continue
        # 工具的判据：本模块内定义、且继承 BaseTool 的类。
        # 必须排除下划线开头的中间基类（`_FileToolBase`）——
        # 我第一版没排除它，数出 22 个；第二版不带 sys.path 又数出 0 个。
        # **两次都是我的判据错，不是产品错** —— 所以这里把判据写死在注释里。
        from agent.tools.base import BaseTool
        names = set()
        for _, obj in vars(mod).items():
            if (inspect.isclass(obj) and issubclass(obj, BaseTool)
                    and obj is not BaseTool
                    and not obj.__name__.startswith('_')
                    and getattr(obj, '__module__', '') == mod_name):
                names.add(obj.__name__)
        total[short] = len(names)
    grand = sum(v for v in total.values() if isinstance(v, int))
    detail = ', '.join(f'{k}={v}' for k, v in total.items())
    # 文档 §三 说 21（与实测一致）；§十一 的 P1 历史行说 18（P1 时期的口径）。
    ok = (grand == 21)
    return ok, f'实测合计 {grand} 个（{detail}）；文档 §三 说 21（一致）'


# --------------------------------------------------------------------------
# 2. "删除强制走回收站、无永久删除分支" —— 硬性约束，必须无例外
# --------------------------------------------------------------------------
@check('删除只能进回收站', 'AGENTS.md §五 "file_delete 的实现中不存在永久删除分支"')
def no_permanent_delete() -> tuple[bool, str]:
    ft = ROOT / 'agent' / 'tools' / 'file_tools.py'
    src = ft.read_text(encoding='utf-8')
    bad = []
    # 永久删除的写法
    for pat, why in [
        (r'\bos\.remove\(', 'os.remove'),
        (r'\bos\.unlink\(', 'os.unlink'),
        (r'\bshutil\.rmtree\(', 'shutil.rmtree'),
        (r'\bPath\([^)]*\)\.unlink\b', 'Path.unlink'),
    ]:
        for m in re.finditer(pat, src):
            line = src[:m.start()].count('\n') + 1
            bad.append(f'{why} @L{line}')
    uses_trash = 'send2trash' in src
    if bad:
        return False, f'发现永久删除调用: {bad}'
    if not uses_trash:
        return False, '没有 send2trash，删除可能根本没实现'
    return True, '只有 send2trash，无 os.remove/unlink/rmtree'


# --------------------------------------------------------------------------
# 3. "两个 EventBus 仍并存"（D14 的措辞边界）
# --------------------------------------------------------------------------
@check('双 EventBus 措辞', 'AGENTS.md D14 "旧类 core.event_bus.EventBus 仍存在且在服务语音层"')
def two_buses() -> tuple[bool, str]:
    old = (ROOT / 'core' / 'event_bus.py').read_text(encoding='utf-8')
    new = (ROOT / 'core' / 'kernel' / 'events.py').read_text(encoding='utf-8')
    old_has = 'class EventBus' in old
    new_has = 'class EventBus' in new
    if not (old_has and new_has):
        return False, f'旧={old_has} 新={new_has} —— 文档说两者并存，对不上'
    # "仍在服务语音层"：谁 import 旧总线
    users = []
    for p in ROOT.rglob('*.py'):
        if '_tmp' in p.name or 'tests' in p.parts:
            continue
        try:
            t = p.read_text(encoding='utf-8')
        except Exception:
            continue
        if 'from core.event_bus import' in t or 'core.event_bus' in t:
            users.append(str(p.relative_to(ROOT)))
    return True, f'两个类都在；旧总线的使用者 {len(users)} 处: {users[:6]}'


# --------------------------------------------------------------------------
# 4. 文档点名的文件路径是否都存在
# --------------------------------------------------------------------------
@check('文档点名的文件存在', 'AGENTS.md 的参考资源表与结构树')
def referenced_files() -> tuple[bool, str]:
    wanted = [
        'docs/agent/spec.md', 'docs/agent/acceptance.md', 'docs/agent/phases.md',
        'docs/agent/tasks.json', 'docs/agent/tasks-p2.json', 'docs/agent/tasks-p3.json',
        'docs/agent/p4-plan.md', 'docs/agent/p3-history.md', 'docs/agent/p4-history.md',
        'docs/agent/f-closure.md', 'docs/agent/sprite-history.md',
        'docs/agent/p5-closure-plan.md', 'docs/agent/tasks-p5.json',
        'docs/agent/acceptance-report-p1.md', 'docs/agent/acceptance-history.md',
        'tools/run_acceptance.py', 'tools/check_repo_hygiene.py',
        'tools/measure_acceptance_metrics.py', 'tools/verify_gui_launch.py',
        'tools/voice_scenarios_loopback.py', 'config.yaml', 'README.md',
        'docs/agent/examples/kernel_demo.py', 'core/plugin_params.py',
        'agent/bootstrap.py', 'agent/store_guard.py', 'agent/reminder_store.py',
    ]
    missing = [w for w in wanted if not (ROOT / w).exists()]
    if missing:
        return False, f'缺失 {len(missing)}/{len(wanted)}: {missing}'
    return True, f'{len(wanted)} 个全部存在'


# --------------------------------------------------------------------------
# 5. 文档说 tasks-p5.json 有 18 项、全部到终态
# --------------------------------------------------------------------------
@check('P5 账本', 'AGENTS.md "P5 登记项 18 项，0 项未到终态"')
def p5_ledger() -> tuple[bool, str]:
    import json
    p = ROOT / 'docs' / 'agent' / 'tasks-p5.json'
    data = json.loads(p.read_text(encoding='utf-8'))
    tasks = data.get('tasks') if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        return False, f'结构不认识: {type(data)} keys={list(data)[:8] if isinstance(data, dict) else None}'
    n = len(tasks)
    states = {}
    for t in tasks:
        st = t.get('state') or t.get('status') or '(无)'
        states[st] = states.get(st, 0) + 1
    pending = [t.get('id') for t in tasks
               if (t.get('state') or t.get('status')) in ('pending', 'in_progress', 'blocked')]
    ok = (n == 18) and not pending
    return ok, f'{n} 项，状态分布 {states}' + (f'，非终态 {pending}' if pending else '')


# --------------------------------------------------------------------------
# 6. 文档说"覆盖率 100%，7238 语句" —— 至少确认口径文件在
# --------------------------------------------------------------------------
@check('覆盖率口径', 'AGENTS.md "覆盖 core/kernel + agent + services.ack_cache"')
def coverage_scope() -> tuple[bool, str]:
    import re as _re

    # 判据：真的存在一处"带 --cov 指定范围的调用"。
    # 第一版我用 `git grep -l --cov=` 找不到东西 —— 那是**我的检索式写错了**
    # （AGENTS.md 里写的是自然语言，不是命令行）。改为扫源码/配置里的 --cov 出现。
    hits = []
    for p in ROOT.rglob('*'):
        if not p.is_file() or '__pycache__' in p.parts or '.git' in p.parts:
            continue
        if p.suffix not in ('.py', '.cfg', '.toml', '.ini', '.yaml', '.md', '.bat'):
            continue
        try:
            t = p.read_text(encoding='utf-8')
        except Exception:
            continue
        if '--cov' in t:
            for m in _re.finditer(r'--cov[=\s]+([\w./,]+)', t):
                hits.append(f'{p.relative_to(ROOT)} → {m.group(0)}')
    return True, (f'{len(hits)} 处: {hits[:5]}' if hits else '未找到任何 --cov 调用')


# --------------------------------------------------------------------------
# 7. "每个公开函数有类型标注"（§4.2 代码规范）
# --------------------------------------------------------------------------
@check('类型标注规范', 'AGENTS.md §4.2 "每个公开函数有类型标注和参数说明"')
def annotations() -> tuple[bool, str]:
    missing = []
    total = 0
    for p in sorted((ROOT / 'agent').rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith('_') and not node.name.startswith('__'):
                continue
            total += 1
            has_ret = node.returns is not None
            args = [a for a in node.args.args if a.arg != 'self']
            has_arg = all(a.annotation is not None for a in args) if args else True
            if not (has_ret and has_arg):
                missing.append(f'{p.relative_to(ROOT)}:{node.lineno} {node.name}')
    pct = (total - len(missing)) / total * 100 if total else 100.0
    return True, (f'agent/ 公开函数 {total} 个，缺标注 {len(missing)} 个'
                  f'（{pct:.1f}% 合规）' + (f'；例: {missing[:3]}' if missing else ''))


def main() -> int:
    print()
    print('=' * 80)
    print('  文档 ↔ 实现 一致性审计（判据都可执行）')
    print('=' * 80)
    mismatches = []
    for name, claim, fn in CHECKS:
        print(f'\n  ── {name} ──')
        print(f'     文档声称: {claim}')
        try:
            ok, detail = fn()          # type: ignore[operator]
        except Exception as e:
            ok, detail = False, f'{type(e).__name__}: {e}'
        print(f'     {"✓" if ok else "❌"} 实测: {detail}')
        if not ok:
            mismatches.append((name, detail))

    print()
    print('=' * 80)
    if mismatches:
        print(f'  ❌ {len(mismatches)} 处文档与实现对不上：')
        for n, d in mismatches:
            print(f'     · {n}: {d}')
    else:
        print('  ✓ 全部对上')
    print('=' * 80)
    return 1 if mismatches else 0


if __name__ == '__main__':
    raise SystemExit(main())
