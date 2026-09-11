# -*- coding: utf-8 -*-
"""把 P5-B5 两个探针的**原始 stdout** 归档。

为什么单独存一份：`serp_parser.txt` 是我对实测的**叙述**，
而这两个探针的输出是**机器直接打出来的原始数据**。
本项目吃过"统计类结论只报一个数、列不出证据"的亏（§16.4），
所以判据的原始输出要与叙述分开留档，能互相校验。

顺带：这里也留档"第一版反方向断言为什么是错的"——
`_tmp_b5_layer_probe.py` 的输出里那句
`与现状相同？True` 就是现场。
"""
import io
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "serp_parser_raw_probes.txt"

PROBES = [
    ("_tmp_b5_recon.py", "第 0 步：8 份原文清单 + 与 P4 记录对账"),
    ("_tmp_b5_layer_probe.py", "第 1 步：三层 × 两模式，第 1 层到底改变了什么"),
    ("_tmp_b5_docstring_probe.py", "第 2 步：docstring 那句「只用 h2/h3 会命中页脚」能否复核"),
]

L = []
w = L.append
w("═" * 78)
w("P5-B5 原始探针输出（未加工）")
w("═" * 78)
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("可复跑：`python docs/agent/evidence/p5/_tmp_b5_raw_probes.py`")
w("")
w("这三个脚本的输出是**证据本体**；`serp_parser.txt` 是对它的叙述。")
w("两者分开留档，是为了让「结论」与「原始数据」能互相校验 ——")
w("凡是统计类结论都要能逐条列出证据，而不是只报一个数。")

for name, title in PROBES:
    p = ROOT / "docs" / "agent" / "evidence" / "p5" / name
    w("")
    w("=" * 78)
    w(f" {title}")
    w(f" 脚本：docs/agent/evidence/p5/{name}")
    w("=" * 78)
    w("")
    if not p.is_file():
        w(f"**脚本不存在：{name}**")
        continue
    proc = subprocess.run([sys.executable, str(p)], cwd=str(ROOT),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    w("```")
    w((proc.stdout or "").rstrip())
    if proc.stderr:
        w("")
        w("[stderr]")
        w(proc.stderr.rstrip())
    w("```")
    w("")
    w(f"退出码：{proc.returncode}")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
