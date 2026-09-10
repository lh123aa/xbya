"""状态文件守卫：**唯一**定义"状态文件不许写到哪里"

## 为什么单独抽出来

这条规则原先在两个文件里各写了一遍（`agent/tracker_store.py` 与
`agent/providers/memory/recall_store.py`），P4-A2 要给提醒存储加第三遍。

三份拷贝的**安全规则**是最危险的一种重复：将来若有人把第 5 个目录
（比如 `videos`）加进其中一份，另两份会**静默地**继续允许写入用户数据区 ——
而"状态文件污染用户目录"恰恰是本项目 R1（误操作损坏文件）要挡的事情之一。

所以抽成一个模块，让三处共用同一份定义；改规则只需要改这里。

## 规则内容

用户白名单的四个目录（桌面/文档/下载/图片）是**用户的数据区**，
Agent 的运行时状态（实体栈、审计库、记忆库、提醒库）一律不许落在里面 ——
否则"宠物在你桌面上悄悄建了个文件"，用户既没同意也不知道怎么清。
`recall_store` 的注释里还有一条更实际的考虑：状态文件若落在用户的搜索结果范围内，
会被自己的 `file_search` 工具搜出来，变成噪音。
"""

from __future__ import annotations

from pathlib import Path

#: 禁止写入的用户目录名（与安全白名单同源）
FORBIDDEN_DIR_NAMES = ("desktop", "documents", "downloads", "pictures")


def is_inside_forbidden_dir(path: Path) -> bool:
    """路径是否落在用户桌面/文档/下载/图片下（含其任意层级子目录）"""
    parts = [p.lower() for p in path.parts]
    return any(name in parts for name in FORBIDDEN_DIR_NAMES)
