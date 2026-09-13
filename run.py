"""
欣雅桌面智能管家 v2.0 - 启动入口
"""

import os
import sys
import logging

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

#: 日志同时写文件与标准输出。
#:
#: **为什么必须写文件**：无窗口启动用的是 `pythonw.exe`，它**根本没有标准输出**
#: （`sys.stdout` 为 None），任何 `print`/traceback 都会**直接消失**。
#: 于是"双击后什么都没发生"成了唯一现象，排查无从下手。
#: 有了这个文件，「启动欣雅.vbs」才能在秒退时把最后几行读出来弹给你看。
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "xbya.log")


def _setup_logging() -> None:
    """配置日志：文件 + 控制台。文件失败也不能拖垮启动。"""
    handlers = []
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # 每次启动覆盖，避免日志无限增长；排查要看的是"这一次"
        handlers.append(logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"))
    except Exception:
        pass
    # pythonw 下 sys.stdout 为 None，加 StreamHandler 会报错，必须判空
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    if handlers:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%H:%M:%S',
            handlers=handlers,
        )


_setup_logging()

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
        # pythonw（无控制台）下，这里是**唯一**能留下痕迹的地方：
        # 日志已写进 logs/xbya.log，「启动欣雅.vbs」会在秒退时读它弹窗。
        logger.error(f"启动失败: {e}")
        import traceback
        tb = traceback.format_exc()
        logger.error(tb)
        # 双保险：万一 logging 本身没配起来（例如 logs 目录不可写），
        # 直接往文件里补一段，保证"为什么失败"一定留得下。
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n[启动失败] {type(e).__name__}: {e}\n{tb}\n")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()