"""C4 辅助探针（**次要证据**，非主判据）

目的有两个，都是为了防止主结论是"空转"：
1. **反方向对照**：同一个探针跑 bing（项目默认引擎）—— 如果连 bing 也 0 条，
   那说明"测量/环境"有问题，而不是"百度谷歌被反爬"。
2. **排除"网络不通"**：直接做 DNS + TLS 握手，证明这两个域名从本机是**通的**，
   所以"返回拦截页"不能解释成"网络不通"。
"""

import os
import socket
import ssl
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools import browser_tools as bt  # noqa: E402
from tools._tmp_c4_probe_search import run_engine  # noqa: E402


def check_dns_tls() -> None:
    print("=" * 78)
    print("[网络可用性] DNS 解析 + TLS 握手（用来排除'网络不通'这一档）")
    print("=" * 78)
    for host in ("www.baidu.com", "wappass.baidu.com", "www.google.com",
                 "www.bing.com"):
        try:
            t0 = time.perf_counter()
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            ips = sorted({i[4][0] for i in infos})
            dns_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=8) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as tls:
                    ver = tls.version()
            tls_ms = (time.perf_counter() - t1) * 1000
            print(f"  {host:22} DNS OK {dns_ms:7.1f}ms ips={ips} "
                  f"TLS OK {tls_ms:7.1f}ms {ver}")
        except Exception as e:                                   # pragma: no cover
            print(f"  {host:22} 失败 {type(e).__name__}: {e}")


def main() -> int:
    check_dns_tls()
    # 用英文查询做反方向对照：与 tools/verify_f4_f5_real.py 的 F4-c 一致
    # （中文查询下 bing 会回"标题对、结果是别人 SERP"的诱饵页，
    #   那是 browser_tools.py:254 已登记的另一个现象，会掩盖"探针能否识别成功"）
    query = sys.argv[1] if len(sys.argv) > 1 else "Python 3.12 release notes"
    print("\n" + "=" * 78)
    print(f"[反方向对照] 同一探针跑 bing（项目默认引擎），query={query!r}")
    print("  若 bing 能拿到结果 → 探针不是空转，百度/谷歌的 0 条是引擎侧行为")
    print("=" * 78)
    rows = run_engine("bing", query, times=1, gap_s=0)
    if rows:
        r = rows[0]
        print(f"\n  bing 判定：{r['flags']['verdict']}")
        print(f"  首条：{r['parser_output'][0] if r['parser_output'] else '（无）'}")
        ok = r["flags"]["verdict"] == "可为"
        print("\n  ★ 反方向对照：",
              "通过（同一探针能把 bing 判成『可为』，说明探针能识别成功，不是只会说『被拦』）"
              if ok else "未通过：bing 也拿不到 → 环境/探针可疑")
        return 0 if ok else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
