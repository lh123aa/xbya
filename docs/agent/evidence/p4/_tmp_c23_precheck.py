"""C2/C3 前置检查：包在不在、HF 通不通、模型缓存在哪"""

import importlib.util
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

print("=" * 74)
print("C2 / C3 前置检查")
print("=" * 74)

for pkg in ("sentence_transformers", "torch", "huggingface_hub", "faster_whisper"):
    spec = importlib.util.find_spec(pkg)
    print(f"  {pkg:<24} {'已安装' if spec else '**未安装**'}")

print()
print("HF 缓存目录:")
hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
print("  HF_HOME =", hf_home)
hub = Path(hf_home) / "hub"
if hub.exists():
    for d in sorted(hub.iterdir()):
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"    {d.name:<50} {size / 1e6:8.1f} MB")
else:
    print("    （hub 目录不存在）")

print()
print("HF 连通性（只发一个 HEAD/GET，几秒内出结果）:")
try:
    import requests
    r = requests.get("https://huggingface.co/api/models/Systran/faster-whisper-medium",
                     timeout=20)
    print(f"  huggingface.co HTTP {r.status_code}  大小={len(r.content)} 字节")
    if r.status_code == 200:
        data = r.json()
        print("  模型 id:", data.get("modelId") or data.get("id"))
        sib = data.get("siblings") or []
        print(f"  文件数: {len(sib)}")
except Exception as e:
    print(f"  失败: {type(e).__name__}: {e}")

print()
print("C2 需要的磁盘（medium ~1.5GB）:")
import shutil
free = shutil.disk_usage(str(PROJECT)).free
print(f"  项目所在盘剩余: {free / 1e9:.1f} GB")
