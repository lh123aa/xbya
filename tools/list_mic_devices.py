# -*- coding: utf-8 -*-
r"""麦克风设备列表 / 试听 / 选择工具。

多声源环境（本机麦克风 + 远程桌面虚拟麦克风 + 混音回环）下，
"自动挑选"未必选到能收到你声音的那个。本工具让你**看见**每个声源、
**试听**谁真的有声音、并把选择写进 config.yaml。

## 用法

    # 1) 列出所有输入设备（标出当前使用中的那个）
    python tools/list_mic_devices.py

    # 2) 逐个试听：采样每个设备的音量，找出"说话时哪个在跳"
    python tools/list_mic_devices.py --probe

    # 3) 选定设备（写进 config.yaml 的 voice.mic_device）
    python tools/list_mic_devices.py --set 1
    python tools/list_mic_devices.py --set "UU远程"

    # 4) 恢复自动挑选
    python tools/list_mic_devices.py --set auto

## 判据（--probe 输出怎么读）

    说话时 **峰值明显跳动** 的那个才是你的麦克风。恒定不变的底噪、
    或者峰值接近 0 的，都不是。远程桌面场景下，
    本机物理麦克风往往是"恒定底噪"（收不到远端声音）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.microphone_service import MicrophoneService  # noqa: E402


def cmd_list(svc: MicrophoneService) -> int:
    print("=" * 74)
    print("输入设备清单")
    print("=" * 74)
    print(svc.describe_devices())
    print()
    devs = svc.list_input_devices()
    if not devs:
        print("未发现任何输入设备。")
        return 1
    print("提示：`回环` 类设备收到的是**系统播放的声音**，不是人声，通常不要选。")
    print("      `[系统默认]` 是 Windows 的默认输入设备。")
    return 0


def cmd_probe(svc: MicrophoneService, seconds: float) -> int:
    devs = svc.list_input_devices()
    if not devs:
        print("未发现任何输入设备。")
        return 1
    total = len(devs) * (seconds + 0.3)
    print("=" * 74)
    print("逐个试听：将对 %d 个设备各采样 %.1f 秒" % (len(devs), seconds))
    print("请【全程持续说话】，约 %.0f 秒" % total)
    print("=" * 74)
    input("准备好后按回车开始…")

    results = []
    idx_list = [d["index"] for d in devs]
    # 逐个采样（并行会崩），并在每个设备前提示
    for i, dev in enumerate(devs, 1):
        print("\n[%d/%d] idx=%d (%s) —— 请说话…" % (
            i, len(devs), dev["index"], dev["name"]), flush=True)
        r = svc.probe_device_levels(seconds=seconds, indices=[dev["index"]])
        if r:
            results.append(r[0])

    print("\n" + "=" * 74)
    print("试听结果（按峰值降序）")
    print("=" * 74)
    print("%-5s %-9s %-9s %-9s %s" % ("idx", "峰值", "平均", "状态", "设备名"))
    print("-" * 74)
    for r in sorted(results, key=lambda x: x["peak"], reverse=True):
        if not r["ok"]:
            state = "打不开"
        elif r["peak"] < 30:
            state = "无声"
        elif r["peak"] < 300:
            state = "微弱"
        else:
            state = "★有声音"
        print("%-5d %-9.1f %-9.1f %-9s %s%s" % (
            r["index"], r["peak"], r["avg"], state, r["name"],
            ("  [" + r["error"][:30] + "]") if r["error"] and not r["ok"] else ""))

    print()
    good = [r for r in results if r["ok"] and r["peak"] >= 300]
    if not good:
        print("⚠ 没有任何设备在说话时出现明显音量 —— 你的声音可能根本没到这台机器。")
        print("  远程桌面场景请先检查**主控端**的麦克风转发开关。")
    elif len(good) == 1:
        print("✓ 唯一有明显声音的设备是 idx=%d (%s)" % (good[0]["index"], good[0]["name"]))
        print("  建议执行： python tools/list_mic_devices.py --set %d" % good[0]["index"])
    else:
        print("多个设备都有声音，请选**名称像麦克风**的那个（排除回环/混音）：")
        for r in good:
            print("  idx=%-3d %s" % (r["index"], r["name"]))
    return 0


def cmd_set(svc: MicrophoneService, value: str) -> int:
    if value.lower() in ("auto", "none", "null", ""):
        selector = None
    else:
        try:
            selector = int(value)
        except ValueError:
            selector = value

    if not svc.set_input_device(selector):
        print("✗ 设置失败：找不到该设备。先用 --list 查看可用设备。")
        return 1

    # 写回 config.yaml
    # 注意：ConfigManager.set() 内部已经落盘（`_save_config()`），不需要再调 save()。
    try:
        from core.config_manager import get_config_manager
        cm = get_config_manager()
        cm.set("voice.mic_device", selector if selector is not None else None)
        print("✓ 已写入 config.yaml: voice.mic_device = %r" % (selector,))
        print("  重启应用后生效。")
    except Exception as e:
        print("✓ 本次运行已切换，但写入 config.yaml 失败: %s" % e)
        print("  请手动把 voice.mic_device 设为 %r" % (selector,))
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="麦克风设备列表 / 试听 / 选择")
    ap.add_argument("--probe", action="store_true", help="逐个试听，找出有声音的设备")
    ap.add_argument("--seconds", type=float, default=1.5, help="每个设备采样秒数（默认1.5）")
    ap.add_argument("--set", metavar="DEV", help="指定设备：索引 / 名字片段 / auto")
    args = ap.parse_args()

    svc = MicrophoneService()
    if not svc.pyaudio_available:
        print("✗ PyAudio 不可用，无法访问麦克风。")
        return 1

    if args.set is not None:
        return cmd_set(svc, args.set)
    if args.probe:
        return cmd_probe(svc, args.seconds)
    return cmd_list(svc)


if __name__ == "__main__":
    sys.exit(main())
