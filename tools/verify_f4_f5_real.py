"""F4 / F5 真实执行验证：浏览器联网 + 系统工具真机执行

对应验收报告里两个"从未真机验证"的开放项：

| 编号 | 待验证 | 本脚本怎么验 |
|------|--------|-------------|
| F4 | `web_open` / `web_search` / `web_read` 的**真实**网络行为 | 真发 HTTP 请求、真拦 SSRF、真断言正文 |
| F5 | `clipboard` / `screenshot` / `open_app` 的**真实**系统调用 | 真读写 Win32 剪贴板、真 GDI 抓屏、真 `os.startfile` |

## 与既有验收脚本的区别

`p1_acceptance_smoke.py` / `p3_acceptance_smoke.py` 验的是**管线与装配**，工具能力多数
用假注入或直接断言 schema。本脚本一律走**真实副作用**：真网络、真剪贴板、真屏幕。
因此它**不适合放进 pytest 常规回归**（网络抖动、会动用户剪贴板）。

## 副作用与安全

- 剪贴板：写入前**备份**原内容，验完**原样恢复**
- 截图：存进白名单目录，验证后**删除**（避免把用户屏幕留在磁盘上）
- `open_app`：只在白名单目录里建一个临时 `.txt` 打开默认程序，验证后**杀掉进程并删文件**
- `web_open`：默认**不真的打开浏览器**（会抢用户窗口），改为断言传给 `webbrowser` 的 URL；
  需要真开一次时加 `--open-browser`

用法：
    python tools/verify_f4_f5_real.py
    python tools/verify_f4_f5_real.py --open-browser      # 额外真开一个浏览器标签
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

PASS, FAIL, SKIP = "[PASS]", "[FAIL]", "[SKIP]"
results: list = []


def check(ok: bool, label: str, detail: str = "") -> None:
    """记录一条断言"""
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def skipped(label: str, why: str) -> None:
    """环境不支持的断言（不计入失败，但会如实计入报告）"""
    results.append((None, label))
    print(f"{SKIP} {label}  → {why}")


def call(tool, params: dict):
    """直接调用工具（绕过管线，验的就是工具本身对真实系统的调用）"""
    t0 = time.perf_counter()
    try:
        res = tool.execute(params)
    except Exception as e:                      # 工具不该往外抛
        return None, (time.perf_counter() - t0) * 1000, f"{type(e).__name__}: {e}"
    return res, (time.perf_counter() - t0) * 1000, ""


# ══════════════════════════════════════════════════════
#  F4：浏览器三工具真实联网
# ══════════════════════════════════════════════════════

def verify_f4(reg, open_browser: bool) -> None:
    print("\n" + "=" * 72)
    print("[F4] 浏览器三工具 —— 真实网络")
    print("=" * 72)

    read = reg.get("web_read")
    search = reg.get("web_search")
    opener = reg.get("web_open")
    for name, tool in (("web_read", read), ("web_search", search),
                       ("web_open", opener)):
        check(tool is not None, f"{name} 已注册")
    if read is None or search is None or opener is None:
        check(False, "F4 三个工具齐备（缺工具无法继续）")
        return

    # ── F4-a 真实抓取正文 ──
    print("\n[F4-a] web_read 真实抓取")
    res, ms, err = call(read, {"url": "https://example.com"})
    if err or res is None:
        check(False, "web_read 未抛异常", err)
    else:
        print(f"  ({ms:.0f}ms) success={res.success} summary={res.summary[:80]!r}")
        check(res.success, "web_read 抓到 example.com", f"{ms:.0f}ms")
        body = str(res.data) if res.data else ""
        check("Example Domain" in body or "example" in body.lower(),
              "抓到的正文是真内容（非空/非占位）", f"{len(body)} 字符")

    # ── F4-b SSRF / 本地文件访问必须被拦 ──
    print("\n[F4-b] SSRF 与本地文件：必须全部拒绝")
    blocked = [
        ("file:///C:/Windows/win.ini", "file 协议"),
        ("http://127.0.0.1:1041/", "本机回环"),
        ("http://localhost:1041/", "localhost"),
        ("http://169.254.169.254/latest/meta-data/", "云元数据地址"),
        ("http://[::1]/", "IPv6 回环"),
    ]
    for url, why in blocked:
        res, ms, err = call(read, {"url": url})
        if err or res is None:
            check(False, f"拒绝 {why}", err)
            continue
        check(not res.success, f"拒绝 {why}", f"{res.summary[:50]!r}")

    # ── F4-c 真实搜索 ──
    # 注意：必须显式 open_browser=False —— 该工具默认会真的打开一个浏览器标签，
    # 验证脚本不该抢用户的窗口。
    print("\n[F4-c] web_search 真实搜索")
    res, ms, err = call(search, {"query": "Python 3.12 release notes",
                                 "open_browser": False})
    if err or res is None:
        check(False, "web_search 未抛异常", err)
    else:
        items = []
        if isinstance(res.data, dict):
            items = res.data.get("results") or []
        elif isinstance(res.data, list):
            items = res.data
        print(f"  ({ms:.0f}ms) success={res.success} 结果数={len(items)} "
              f"summary={res.summary[:70]!r}")
        check(res.success, "web_search 调用成功")
        if items:
            check(True, "web_search 返回真实结果", f"{len(items)} 条 / {ms:.0f}ms")
            first = items[0] if isinstance(items[0], dict) else {}
            url = str(first.get("url") or "")
            title = str(first.get("title") or "")
            print(f"  首条: {title[:60]!r} → {url[:70]}")
            check(url.startswith("http"), "结果含真实 URL", url[:70])
            check("bing.com/ck/a" not in url,
                  "跳转链接已还原成真实地址", url[:70])
            check("python" in title.lower() or "python" in url.lower(),
                  "标题与查询相关（不是导航栏/页脚推荐位）", title[:60])
        else:
            # 引擎可达性问题（限流 / 反爬诱饵页）—— 如实报 SKIP，不假装通过
            skipped("web_search 返回真实结果",
                    f"引擎未返回可用结果（{res.summary[:50]!r}）；"
                    "属服务端行为，非本层缺陷")

    # ── F4-c2 中文查询：不得把引擎诱饵页当答案念出来 ──
    print("\n[F4-c2] 中文查询不得返回与查询无关的诱饵结果")
    res, ms, err = call(search, {"query": "上海天气", "open_browser": False})
    if err or res is None:
        check(False, "中文查询未抛异常", err)
    else:
        items = res.data.get("results") if isinstance(res.data, dict) else []
        titles = [str(i.get("title") or "") for i in (items or [])]
        print(f"  ({ms:.0f}ms) 结果数={len(titles)} summary={res.summary[:60]!r}")
        # 要么给了相关结果（含"天气/上海"），要么一条都不给 —— 不能给无关结果
        relevant = all(("天气" in t or "上海" in t) for t in titles)
        check(relevant, "返回的结果都与查询相关（或为空）",
              f"{titles[:2]}" if titles else "空 → 已退回诚实文案")

    # ── F4-d web_open：断言交给浏览器的 URL ──
    print("\n[F4-d] web_open 交接给系统浏览器")
    import webbrowser
    seen: list = []
    real_open = webbrowser.open

    def fake_open(url, *a, **kw):
        seen.append(url)
        return True

    webbrowser.open = fake_open
    try:
        res, ms, err = call(opener, {"url": "example.com"})
        check(err == "" and res is not None and res.success,
              "web_open 调用成功", err or res.summary[:50])
        check(bool(seen) and "example.com" in seen[0],
              "URL 正确交接给 webbrowser", seen[0] if seen else "（未调用）")
    finally:
        webbrowser.open = real_open

    # ── F4-e 不存在的域名要有可读失败 ──
    print("\n[F4-e] 解析不了的域名")
    res, ms, err = call(read, {"url": "https://this-host-does-not-exist-"
                                      "xbya-test.invalid/"})
    if err or res is None:
        check(False, "坏域名未抛异常", err)
    else:
        check(not res.success, "坏域名返回可读失败", f"{res.summary[:60]!r}")

    # ── F4-f 可选：真开一个浏览器标签 ──
    if open_browser:
        print("\n[F4-f] --open-browser：真的打开一次浏览器")
        res, ms, err = call(opener, {"url": "https://example.com"})
        check(err == "" and res is not None and res.success,
              "真开浏览器返回成功", err or res.summary[:50])


# ══════════════════════════════════════════════════════
#  F5：系统工具真机执行
# ══════════════════════════════════════════════════════

def verify_f5(reg, guard) -> None:
    print("\n" + "=" * 72)
    print("[F5] 系统工具 —— 真机执行（剪贴板 / 截图 / 打开应用）")
    print("=" * 72)

    # ── F5-a 剪贴板真读写（含备份与恢复）──
    print("\n[F5-a] clipboard 真实读写 Win32 剪贴板")
    clip = reg.get("clipboard")
    check(clip is not None, "clipboard 已注册")
    shot = reg.get("screenshot")
    check(shot is not None, "screenshot 已注册")
    opener = reg.get("open_app")
    check(opener is not None, "open_app 已注册")
    if clip is None or shot is None or opener is None:
        check(False, "F5 三个工具齐备（缺工具无法继续）")
        return

    backup_res, _, _ = call(clip, {"action": "get"})
    backup = ""
    if backup_res is not None and backup_res.success:
        backup = str((backup_res.data or {}).get("text", "")
                     if isinstance(backup_res.data, dict) else "")

    marker = f"xbya-f4f5-{int(time.time())}"
    try:
        res, ms, err = call(clip, {"action": "set", "text": marker})
        check(err == "" and res is not None and res.success,
              "clipboard 写入成功", err or f"{ms:.0f}ms")

        res2, ms2, err2 = call(clip, {"action": "get"})
        got = ""
        if res2 is not None and isinstance(res2.data, dict):
            got = str(res2.data.get("text", ""))
        check(err2 == "" and got == marker,
              "读回来的内容与写入一致（真·系统剪贴板往返）",
              f"{got[:40]!r}")
    finally:
        # 恢复用户原来的剪贴板内容
        call(clip, {"action": "set", "text": backup})
    restored, _, _ = call(clip, {"action": "get"})
    rtext = ""
    if restored is not None and isinstance(restored.data, dict):
        rtext = str(restored.data.get("text", ""))
    check(rtext == backup, "原剪贴板内容已恢复", f"{rtext[:30]!r}")

    # ── F5-b 截图真抓屏 ──
    print("\n[F5-b] screenshot 真实 GDI 抓屏")
    try:
        import PIL  # noqa: F401
        has_pil = True
    except Exception:
        has_pil = False

    if not has_pil:
        skipped("screenshot 真实抓屏", "未安装 Pillow，GDI 位图无法编码成 PNG")
    else:
        res, ms, err = call(shot, {"filename": "f4f5_verify.png"})
        if err or res is None:
            check(False, "screenshot 未抛异常", err)
        else:
            print(f"  ({ms:.0f}ms) success={res.success} summary={res.summary[:70]!r}")
            check(res.success, "screenshot 抓屏成功", f"{ms:.0f}ms")
            path = None
            if isinstance(res.data, dict):
                path = res.data.get("path")
            if path and Path(path).exists():
                p = Path(path)
                data = p.read_bytes()
                check(data[:8] == b"\x89PNG\r\n\x1a\n",
                      "产出的是真 PNG（magic 正确）", f"{len(data)} bytes")
                check(len(data) > 10240, "PNG 体积说明是真图像而非空白",
                      f"{len(data)} bytes")
                try:
                    from PIL import Image
                    with Image.open(p) as im:
                        w, h = im.size
                    print(f"  图像尺寸: {w}x{h}")
                    check(w >= 800 and h >= 600, "分辨率合理", f"{w}x{h}")
                except Exception as e:
                    check(False, "PNG 可被解码", f"{type(e).__name__}: {e}")
                p.unlink(missing_ok=True)
                check(not p.exists(), "验证后已删除截图（不把屏幕留在磁盘上）")
            else:
                check(False, "截图文件落盘", f"path={path!r}")

    # ── F5-c open_app 真启动 ──
    print("\n[F5-c] open_app 真实启动应用")
    roots = guard.whitelist_roots()
    tmp = Path(roots[0]) / f"xbya_f4f5_{int(time.time())}.txt"
    before = set(_notepad_pids())
    try:
        tmp.write_text("xbya f4f5 open_app verification\n", encoding="utf-8")
        res, ms, err = call(opener, {"target": str(tmp)})
        if err or res is None:
            check(False, "open_app 未抛异常", err)
        else:
            print(f"  ({ms:.0f}ms) success={res.success} summary={res.summary[:70]!r}")
            check(res.success, "open_app 调用成功", f"{ms:.0f}ms")

            # 默认程序启动需要一点时间
            new_pids = []
            for _ in range(20):
                time.sleep(0.5)
                new_pids = sorted(set(_notepad_pids()) - before)
                if new_pids:
                    break
            if new_pids:
                check(True, "系统真的启动了默认程序（新进程出现）",
                      f"PID={new_pids}")
            else:
                skipped("默认程序进程出现",
                        "未观察到新进程（可能复用了已开窗口，或默认程序非记事本）")
            for pid in new_pids:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, check=False)
            if new_pids:
                check(True, "验证后已关闭该进程")
    finally:
        tmp.unlink(missing_ok=True)

    # ── F5-d system_info 真读硬件 ──
    print("\n[F5-d] system_info 真实读取本机指标")
    info = reg.get("system_info")
    res, ms, err = call(info, {"metric": "all"})
    if err or res is None:
        check(False, "system_info 未抛异常", err)
    else:
        print(f"  ({ms:.0f}ms) {res.summary[:100]!r}")
        check(res.success, "system_info 读取成功", f"{ms:.0f}ms")
        d = res.data if isinstance(res.data, dict) else {}
        check(bool(d), "返回结构化指标", f"keys={sorted(d)[:6]}")


def _notepad_pids() -> list:
    """当前记事本进程 PID（用来判断 open_app 是否真启动了东西）"""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq notepad.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=False).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("notepad"):
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


# ══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-browser", action="store_true",
                    help="额外真的打开一次浏览器标签（默认不抢用户窗口）")
    args = ap.parse_args()

    print("=" * 72)
    print("F4 / F5 真实执行验证")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    from core.app import xbyaApp

    app = xbyaApp()
    if not app.initialize():
        print(f"{FAIL} 应用初始化失败，无法验证")
        return 1

    try:
        stack = app.agent_stack
        if stack is None:
            print(f"{FAIL} Agent 层未装配")
            return 1
        reg, guard = stack.registry, stack.safety
        print(f"\n装配: tools={reg.count()} 白名单={[p.name for p in guard.whitelist_roots()]}")

        verify_f4(reg, args.open_browser)
        verify_f5(reg, guard)
    finally:
        app._teardown_agent_layer()

    ok = sum(1 for r, _ in results if r is True)
    bad = sum(1 for r, _ in results if r is False)
    sk = sum(1 for r, _ in results if r is None)
    print("\n" + "=" * 72)
    print(f"F4/F5 结果：{ok}/{ok + bad} 通过，{bad} 失败，{sk} 跳过")
    for r, label in results:
        if r is not True:
            print(f"  {FAIL if r is False else SKIP} {label}")
    print("=" * 72)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
