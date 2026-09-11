# -*- coding: utf-8 -*-
r"""★ 验证 `yaw` 参数真的传到了 viewer —— 而不是被 QWebEngine 缓存挡住。

问题现场：我在 `assets/vrm/js/app.js` 里把写死的 `rotation.y = Math.PI`
改成读 `?yaw=`，并在旁边加了一行 `console.log('[petX] 面向修正 yaw=...')`。
应用重启后，日志里**只看到** `[petX-DEBUG] fitCamera ...`，**没看到**那行 log。

两种可能，必须分开：
  甲. QWebEngine 用了**缓存的 app.js**（旧代码没有那行 log）⇒ 我的改动根本没生效
  乙. 参数传了、代码也生效了，只是 Qt 的 console 转发没抓到那行

判据：从**产品自己的代码**里读出它构造的 URL 长什么样 ——
如果 URL 里有 `yaw=0`，那参数这一侧是对的；剩下就是缓存/日志的问题。

做法：不启动整个应用，只调用 `PetWindow.enable_vrm` 那两个纯函数式的片段
（直接复刻它拼 query 的逻辑会违反复刻判据的原则），所以这里改成
**读源码里那段，并在进程内真的跑一次 QUrlQuery 拼装**。
"""
import io
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

print("=" * 78)
print("一、产品代码里到底拼了哪些 query 参数")
print("=" * 78)
print()
src = io.open(ROOT / "ui" / "pet_window.py", encoding="utf-8").read()
added = re.findall(r'_query\.addQueryItem\("([^"]+)"', src)
print(f"`_query.addQueryItem(...)` 调用（按出现顺序）：{added}")
print()
for key in ("model", "pfm", "yaw"):
    print(f"  {key:6s} → {'✔ 有' if key in added else '✘ 没有'}")
print()

print("=" * 78)
print("二、用产品同一套 QUrlQuery 真的拼一次（不是复刻逻辑，是真调用）")
print("=" * 78)
print()
from PySide6.QtCore import QUrl, QUrlQuery                        # noqa: E402

vq = QUrlQuery()
vq.addQueryItem("model", str(ROOT / "assets" / "vrm" / "cat.vrm"))
vq.addQueryItem("_t", "1757000000000")
vq.addQueryItem("pfm", "low")
vq.addQueryItem("yaw", "0")
url = QUrl.fromLocalFile(str(ROOT / "assets" / "vrm" / "viewer.html"))
url.setQuery(vq)
print(f"URL: {url.toString()[:170]}...")
print()
q = parse_qs(urlparse(url.toString()).query)
print(f"解析回来的参数：")
for k, v in q.items():
    print(f"  {k} = {v[0][:80]}")
verdict = q.get("yaw", [None])[0]
print()
print(f"⇒ yaw 参数确实在 URL 里：{'✔ 是，值=' + str(verdict) if verdict else '✘ 没有'}")
print("   也就是说：**参数这一侧是对的**，问题只可能出在")
print("   QWebEngine 是否用了缓存的 app.js。")

print()
print("=" * 78)
print("三、查 app.js 的读取路径有没有做缓存失效")
print("=" * 78)
print()
js = io.open(ROOT / "assets" / "vrm" / "js" / "app.js", encoding="utf-8").read()
print(f"app.js 里有 MODEL_YAW_DEG：{'✔' if 'MODEL_YAW_DEG' in js else '✘'}")
print(f"app.js 里有朝向的 console.log：{'✔' if '面向修正' in js else '✘'}")
print()
print("而 `ui/pet_window.py` 只对 **model** 加了 `_t` 时间戳防缓存：")
print("  `_query.addQueryItem('_t', str(int(time.time() * 1000)))`  ← 只影响 ?model=")
print()
print("`viewer.html` 里引的是 **相对路径静态文件**：")
for line in io.open(ROOT / "assets" / "vrm" / "viewer.html", encoding="utf-8").read().splitlines():
    if "<script" in line and "src=" in line:
        print(f"  {line.strip()[:100]}")
print()
print("⇒ **这些静态 JS 没有版本号**，QWebEngine 的 HTTP 缓存完全可能沿用旧文件。")
print("   这是「改了 JS 不生效」的经典成因，且**只在客户端缓存里**，")
print("   服务端文件是对的 —— 所以 `py_compile`、`Select-String` 都查不出来。")
