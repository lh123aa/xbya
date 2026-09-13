"""
欣雅桌面智能管家 v2.0 - 启动入口
"""

import os
import sys
import logging

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger(__name__)


def check_dependencies():
    missing = []
    for mod, pkg in [("yaml", "pyyaml"), ("psutil", "psutil"),
                     ("PySide6", "PySide6"), ("edge_tts", "edge-tts"),
                     ("requests", "requests")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[安装] 正在安装缺失依赖: {', '.join(missing)}")
        import subprocess
        for pkg in missing:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])
        print("[完成] 依赖安装完成\n")
    return True


def main():
    os.chdir(PROJECT_ROOT)
    check_dependencies()

    try:
        from core.app import xbyaApp
        xbya = xbyaApp("config.yaml")
        xbya.run()
    except Exception as e:
        # pythonw(无控制台)下不能用 input/依赖终端的输出，改用日志
        logger.error(f"启动失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()