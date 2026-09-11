# -*- coding: utf-8 -*-
"""P5-B2 证据：语音服务那条加载路径到底有没有传参

为什么要单独跑一遍"真实装配"而不只靠单元测试：这里的缺陷特征是**静默**——
`PluginLoader.load(name)`（不带 params）不会报错、不会告警，插件只是安静地用
自己 `__init__` 的默认值。所以在**改动前**必须先把"它确实没传"记录下来，
否则"修好了"就只是一句说法。

做法：把 `PluginLoader.load` 换成**只记录 kwargs 的假实现**，然后跑
`VoiceService.initialize()`。假实现不加载任何模型（毫秒级），
但我们能看到**真实调用点传了什么**。

输出分两节：
  甲、当前代码（已修）传了什么
  乙、把 `services/voice_service.py` 临时改回原始写法后传了什么 —— 证明这条证据抓得住
"""
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xiaoyi-vrm-worktree")
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "voice_service_params.txt"
VS = ROOT / "services" / "voice_service.py"

PROBE = r'''
import sys
from pathlib import Path
sys.path.insert(0, r"{ROOT}")

from core.config_manager import get_config_manager
import services.voice_service as vs

CALLS = []

class _RecordingLoader:
    def load(self, name, params=None):
        CALLS.append((name, params))
        return object()          # 非 None 即视为成功，避免走 load_by_interface
    def load_by_interface(self, iface):
        CALLS.append((iface, "<by_interface>"))
        return object()

class _VoiceService(vs.VoiceService):
    def __init__(self):
        # 绕开单例 getter，直接注入我们要观察的对象
        self.event_bus = None
        self.plugin_loader = _RecordingLoader()
        self.config_manager = get_config_manager()
        self.asr = None
        self.tts = None
        self.recording = False
        self.audio_buffer = []
        self.on_speech_recognized = None
        self.on_speech_synthesized = None

svc = _VoiceService()
ok = svc.initialize()
print("initialize() ->", ok)
cfg = get_config_manager()
print("config 里 asr.params =", cfg.get("plugins.asr.params"))
print("---- 实际传给 PluginLoader.load 的东西 ----")
for name, params in CALLS:
    print(f"  load({name!r}, params={params!r})")

asr_calls = [p for n, p in CALLS if p != "<by_interface>"]
if not asr_calls:
    print("[判定] 没有任何完整 load 调用")
else:
    first = asr_calls[0]
    if first is None:
        print("[判定] ❌ ASR 加载**没有传 params** —— config 里的 model_size / initial_prompt 静默失效")
    else:
        keys = sorted(first)
        print(f"[判定] ✅ ASR 加载带了 params，键：{keys}")
        print(f"          model_size={first.get('model_size')!r}")
        print(f"          initial_prompt={first.get('initial_prompt')!r}")
'''


def run_probe() -> str:
    # 用 replace 而不是 str.format：PROBE 里有 `{name!r}` 这种**探针自己的**占位符，
    # 而 format 会把它们当成待填字段（第一次跑就 KeyError: 'name'）。
    src = PROBE.replace("{ROOT}", str(ROOT))
    proc = subprocess.run([sys.executable, "-c", src],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")


lines = []
w = lines.append
w("# P5-B2 证据：语音服务加载 ASR 时到底传没传 params")
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("")
w("## 背景（为什么这条缺陷值得单独留证）")
w("")
w("`PluginLoader.load(name, params=None)` 在 `params` 为空时走 `plugin_class()` ——")
w("**一个配置都不带**。`services/voice_service.py` 原先正是这样：")
w("")
w("```python")
w("self.asr = self.plugin_loader.load(asr_engine)      # ← 没有 params=")
w("```")
w("")
w("于是 `config.yaml` 里的 `plugins.asr.params.model_size`（medium）与")
w("`initial_prompt`（P4-B5 加的解码偏置）在这条路径上**完全没被读到**，")
w("插件退回 `__init__` 默认值（`model_size=\"base\"`、`initial_prompt=\"\"`）。")
w("**而且完全没有告警** —— 日志里只写「ASR插件加载成功: faster_whisper」。")
w("")
w("这解释了 P4-C2 那条一直没讲通的旧记录：**「config 写 medium，回环实际用 small」**。")
w("")
w("## 甲、当前代码（已修）")
w("")
w("探针把 `PluginLoader.load` 换成只记录 kwargs 的假实现，再跑 `initialize()`。")
w("假实现不加载任何模型，所以是毫秒级；但它如实反映**真实调用点传了什么**。")
w("")
w("```")
w(run_probe().rstrip())
w("```")
w("")

# ── 乙、反方向：临时改回原始写法 ──
orig = VS.read_text(encoding="utf-8")
w("## 乙、反方向：把 `params=asr_params` 拿掉之后")
w("")
w("如果探针在「不传 params」时也报 ✅，那它就是空转的。")
w("")
try:
    broken = orig.replace(
        "self.plugin_loader.load(asr_engine, params=asr_params)",
        "self.plugin_loader.load(asr_engine)",
    )
    assert broken != orig, "没找到要改的那一行，反方向验证不成立"
    VS.write_text(broken, encoding="utf-8")
    out = run_probe()
    judge = [ln for ln in out.splitlines() if ln.startswith("[判定]")]
    w("```")
    w(out.rstrip())
    w("```")
    w("")
    ok = any("❌" in ln for ln in judge)
    w(f"- 结论：**{'✅ 探针确实照出了「没传 params」（不是空转）' if ok else '❌ 拿掉修复后探针仍报 ✅ —— 证据是空转的'}**")
finally:
    VS.write_text(orig, encoding="utf-8")

w("")
w("## 丙、复原确认")
w("")
after = run_probe()
judge = [ln for ln in after.splitlines() if ln.startswith("[判定]")]
w("```")
w("\n".join(judge))
w("```")
w("")
w(f"- {'✅ 已复原并重新带参' if judge and '✅' in judge[0] else '❌ 没恢复干净'}")
w("")
w("## 丁、单元测试（tests/test_voice_service_params.py，11 项）")
w("")
proc = subprocess.run([sys.executable, "-m", "pytest",
                       "tests/test_voice_service_params.py", "-q"],
                      cwd=ROOT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
w("```")
w(proc.stdout.strip())
w(f"[exit] {proc.returncode}")
w("```")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
print("--- 甲（已修）---")
print("\n".join(run_probe().strip().splitlines()[-4:]))
