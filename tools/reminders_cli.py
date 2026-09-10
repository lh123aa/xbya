"""提醒状态文件的操作小工具（运维 / 验收用）

## 为什么需要它

P4-A2 修的是债务 D13：**提醒只存在内存里，关掉程序再打开就没了**。
要证明"修好了"，最诚实的做法不是在同一进程里换对象图，而是：

    进程 A 设一条提醒 → A 退出 → 进程 B 读得到 → B 里到点仍然会响

而"另起一个进程读写提醒"这件事需要一个入口 —— 就是本脚本。
它同时也是运维工具：用户/我可以直接看现在有哪些待提醒、清掉测试留下的垃圾。

## 用法

    python tools/reminders_cli.py path                       # 打印状态文件路径
    python tools/reminders_cli.py add --what 喝水 --minutes 30
    python tools/reminders_cli.py list
    python tools/reminders_cli.py clear --what 跨进程喝水     # 按内容子串删（可多次）
    python tools/reminders_cli.py clear --all

退出码：0 = 成功；1 = 失败（例如 add 没给出 --what）。输出一律是给人看的纯文本，
便于验收脚本按行解析。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from agent.reminder_store import ReminderStore          # noqa: E402
from agent.tools.productivity_tools import ReminderTool  # noqa: E402


def _tool(path: str | None) -> tuple:
    store = ReminderStore(path=path)
    tool = ReminderTool(store=store)
    restored = tool.restore()
    return tool, store, restored


def cmd_path(args) -> int:
    print(str(ReminderStore(path=args.store).path))
    return 0


def cmd_add(args) -> int:
    tool, _store, _restored = _tool(args.store)
    res = tool.execute({"what": args.what, "minutes": args.minutes})
    if not res.ok:
        # 可达：`--what ""`（空内容）或 `--minutes 0`（非正数）都会走到这里
        print(f"失败: {res.summary}", file=sys.stderr)
        return 1
    data = res.data if isinstance(res.data, dict) else {}
    print(f"已设置 {data.get('id')} | {data.get('when_text')} | {args.what}")
    return 0


def cmd_list(args) -> int:
    tool, _store, _restored = _tool(args.store)
    pending = tool.pending()
    print(f"待提醒 {len(pending)} 条")
    for i in sorted(pending, key=lambda x: x.due_at):
        print(f"{i.id} | {i.when_text} | {i.what}")
    return 0


def cmd_clear(args) -> int:
    tool, _store, _restored = _tool(args.store)
    pending = tool.pending()
    if not pending:
        print("没有待提醒")
        return 0
    removed = 0
    for i in list(pending):
        hit = args.all or any(k and k in i.what for k in (args.what or []))
        if hit and tool.cancel(i.id):
            removed += 1
            print(f"已取消 {i.id} | {i.what}")
    print(f"共取消 {removed} 条")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="提醒状态文件操作（P4-A2 / D13 验收与运维）")
    ap.add_argument("--store", default=None,
                    help="状态文件路径；默认用 agent.reminder_store 的默认路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path", help="打印状态文件路径").set_defaults(func=cmd_path)

    p_add = sub.add_parser("add", help="设一条提醒")
    p_add.add_argument("--what", required=True, help="提醒内容")
    p_add.add_argument("--minutes", type=float, default=30.0, help="多少分钟后（默认 30）")
    p_add.set_defaults(func=cmd_add)

    sub.add_parser("list", help="列出待提醒").set_defaults(func=cmd_list)

    p_clear = sub.add_parser("clear", help="取消提醒")
    p_clear.add_argument("--what", action="append", help="按内容子串匹配（可多次）")
    p_clear.add_argument("--all", action="store_true", help="取消全部")
    p_clear.set_defaults(func=cmd_clear)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
