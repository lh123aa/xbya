"""C4 辅助留档：可见正文长度与结构标记计数（把报告里的数字钉到可复算的方法上）

报告里"谷歌 92KB 但可见正文只有 58 字符"这类数字，必须能给出**具体算法**，
否则就是不可复算的断言。本脚本把算法固定下来：

    可见正文 = 用产品自己的 _DROP_TAG_RE 去掉 <script>/<style>/<noscript>/<head>/
               <iframe> 整段，去掉注释，去掉所有标签，反转义实体，压缩空白

（即 browser_tools.py 里同一套正则，避免我另写一套与之不一致的清洗）
"""

import os
import sys
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools import browser_tools as bt  # noqa: E402

EV = os.path.join(ROOT, "docs", "agent", "evidence", "p4")

MARKERS = {
    "baidu": ['class="result"', "id=\"content_left\"", 'mu="http', "c-container"],
    "google": ['id="search"', 'id="rso"', "<h3", 'class="g"', "data-s"],
    "bing": ['class="b_algo"'],
}
CAPTCHA_MARKERS = ("captcha", "sorry", "consent", "unusual traffic",
                   "检测到", "机器人", "验证", "not a robot")


def visible(html: str) -> str:
    import re
    t = bt._DROP_TAG_RE.sub(" ", html or "")
    t = re.sub(r"(?is)<!--.*?-->", " ", t)
    t = bt._ANY_TAG_RE.sub(" ", t)
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def main() -> int:
    print("=" * 78)
    print("可见正文长度 / 结构标记计数（算法见本文件 docstring）")
    print("=" * 78)
    for engine in ("baidu", "google", "bing"):
        for run in (1, 2, 3):
            path = os.path.join(EV, f"_tmp_c4_raw_{engine}_run{run}.html")
            if not os.path.exists(path):
                continue
            raw = open(path, "rb").read()
            html = raw.decode("utf-8", errors="replace")
            vis = visible(html)
            hits = {m: html.lower().count(m.lower()) for m in MARKERS.get(engine, ())}
            cap = {m: html.lower().count(m.lower()) for m in CAPTCHA_MARKERS
                   if html.lower().count(m.lower())}
            print(f"\n{engine} run{run}: bytes={len(raw)} chars={len(html)} "
                  f"visible_chars={len(vis)}")
            print(f"   结果容器计数: {hits}")
            print(f"   反爬/验证字样计数(仅非零): {cap}")
            print(f"   可见正文全文: {vis[:300]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
