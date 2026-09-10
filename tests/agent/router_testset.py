"""规则路由测试指令集

真实场景指令集，用于验收「规则路由准确率 >90%」。
每条为 (输入文本, 期望 action, 期望参数片段)。

设计说明：
- 用例取自真实使用场景，不构造边缘特例
- 参数断言只检查关键字段（避免过度耦合实现细节）
- 句式保持自然口语，不做人为简化
- P0 收尾 64 条；P1 加入口语化系统状态回归后 73 条
"""

from typing import Any, Dict, List, Tuple

#: (文本, 期望 action, 期望参数子集)
ROUTER_TESTSET: List[Tuple[str, str, Dict[str, Any]]] = [
    # ══════ file_search（10 条）══════
    ("找一下桌面上的合同文件", "file_search", {"pattern": "*合同*", "dirs": ["Desktop"]}),
    ("帮我搜一下下载文件夹里的PDF", "file_search", {"pattern": "*.pdf", "dirs": ["Downloads"]}),
    ("桌面上的图片在哪", "file_search", {"pattern": "*.png"}),
    ("上周的报表文件找一下", "file_search", {"time_range": "last_week"}),
    ("有没有关于预算的文档", "file_search", {"pattern": "*预算*"}),
    ("找找我的简历", "file_search", {"pattern": "*简历*"}),
    ("找一下下载目录里的安装包", "file_search", {"dirs": ["Downloads"]}),
    ("找一下文档文件夹里的Excel", "file_search", {"pattern": "*.xlsx"}),
    ("找一下截图", "file_search", {"pattern": "*截图*"}),
    ("找一下前两天的视频", "file_search", {"time_range": "last_2_days"}),
    ("有没有安装包", "file_search", {}),

    # ══════ file_list（5 条）══════
    ("桌面上有什么", "file_list", {"dirs": ["Desktop"]}),
    ("列出下载文件夹", "file_list", {"dirs": ["Downloads"]}),
    ("文档目录里有什么", "file_list", {"dirs": ["Documents"]}),
    ("图片文件夹里有啥", "file_list", {"dirs": ["Pictures"]}),
    ("显示桌面文件", "file_list", {"dirs": ["Desktop"]}),
    ("下载目录里有什么文件", "file_list", {"dirs": ["Downloads"]}),

    # ══════ file_read（5 条）══════
    ("打开报告.docx", "file_read", {"target": "报告.docx"}),
    ("看看这个文件", "file_read", {}),
    ("读一下简历", "file_read", {"target": "简历"}),
    ("打开第一个", "file_read", {"target": "第一个"}),
    ("看看合同的内容", "file_read", {"target": "合同"}),
    ("查看一下那个报表", "file_read", {}),

    # ══════ file_rename（5 条）══════
    ("把报告改成年终总结", "file_rename", {"source": "报告", "target": "年终总结"}),
    ("重命名这个文件", "file_rename", {}),
    ("把a.txt改成b.txt", "file_rename", {"source": "a.txt", "target": "b.txt"}),
    ("改名成新的", "file_rename", {"target": "新的"}),
    ("把这个文件换个名字", "file_rename", {}),

    # ══════ file_move（5 条）══════
    ("把报告移到文档文件夹", "file_move", {"dest": "Documents"}),
    ("挪到下载目录", "file_move", {"dest": "Downloads"}),
    ("移动到桌面", "file_move", {"dest": "Desktop"}),
    ("把这个移到图片文件夹", "file_move", {"dest": "Pictures"}),
    ("文件挪个位置", "file_move", {}),

    # ══════ file_delete（5 条）══════
    ("删除桌面上的截图", "file_delete", {}),
    ("把那些都删了", "file_delete", {}),
    ("清理一下下载文件夹", "file_delete", {}),
    ("删掉这个文件", "file_delete", {}),
    ("不要这些了，删掉", "file_delete", {}),
    ("把桌面的临时文件清理掉", "file_delete", {}),

    # ══════ 其他工具（5 条）══════
    ("翻译这段话", "translate", {}),
    ("算一下 25 乘 4", "calculate", {"expression": "25 * 4"}),
    ("算一下 128 除以 4", "calculate", {"expression": "128 / 4"}),
    ("内存用了多少", "system_info", {"metric": "memory"}),
    ("复制这段文字", "clipboard", {}),

    # ══════ 口语化系统状态（回归：曾误判为 file_read）══════
    ("看看电脑状态", "system_info", {"metric": "all"}),
    ("电脑状态", "system_info", {"metric": "all"}),
    ("电脑怎么样", "system_info", {"metric": "all"}),
    ("电脑卡不卡", "system_info", {"metric": "all"}),
    ("还有多少电", "system_info", {"metric": "all"}),
    ("查一下电脑状态", "system_info", {"metric": "all"}),
    ("我的电脑还行吗", "system_info", {"metric": "all"}),
    # 反向保障：「看看 + 文件」仍必须是读文件，不能被系统状态抢走
    ("看看电脑上的笔记", "file_read", {}),
    ("看看桌面上的截图", "screenshot", {}),

    # ══════ 系统工具（5 条，P1 新增）══════
    ("打开记事本", "open_app", {"target": "记事本"}),
    ("打开计算器", "open_app", {"target": "计算器"}),
    ("截个图", "screenshot", {}),
    ("执行命令 dir", "run_command", {"command": "dir"}),
    ("磁盘用了多少", "system_info", {"metric": "disk"}),

    # ══════ 生产力工具（4 条，P1 新增）══════
    ("提醒我30分钟后开会", "reminder", {"minutes": 30.0, "what": "开会"}),
    ("提醒我2小时后交报告", "reminder", {"minutes": 120.0, "what": "交报告"}),
    ("北京天气怎么样", "weather", {"city": "北京"}),
    ("帮我查一下天气", "weather", {}),

    # ══════ 浏览器工具（3 条，P1 新增）══════
    ("打开百度", "web_open", {}),
    ("百度一下 深圳天气", "web_search", {"query": "深圳天气"}),
    ("读一下 https://example.com 的内容", "web_read", {"url": "https://example.com"}),

    # ══════ 闲聊兜底（5 条）══════
    ("你好呀", "chat", {}),
    ("今天心情不错", "chat", {}),
    ("给我讲个笑话", "chat", {}),
    ("你觉得我怎么样", "chat", {}),
    ("陪我聊聊天", "chat", {}),

    # ══════ 未实现能力兜底（3 条）══════
    ("帮我发个邮件", "chat", {}),
    ("帮我看看股票", "chat", {}),
    ("放首歌听听", "chat", {}),
]

#: 辅助：按 action 分组统计
def group_by_action() -> Dict[str, int]:
    """统计各 action 的用例数"""
    counts: Dict[str, int] = {}
    for _, action, _ in ROUTER_TESTSET:
        counts[action] = counts.get(action, 0) + 1
    return counts


if __name__ == "__main__":
    print(f"用例总数: {len(ROUTER_TESTSET)}")
    for action, n in sorted(group_by_action().items()):
        print(f"  {action:14s} {n}")
