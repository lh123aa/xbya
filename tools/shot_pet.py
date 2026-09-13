# -*- coding: utf-8 -*-
"""用 PrintWindow(PW_RENDERFULLCONTENT) 抓取桌宠窗口内容到 PNG。
QWebEngine 用 GPU 渲染，CopyFromScreen 常抓不到，PrintWindow+0x2 更可靠。"""
import ctypes, ctypes.wintypes as wt, sys
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def find_pet():
    # 找标题为"欣雅"的窗口，或任一有可见主窗口的 python 进程窗口
    import subprocess, re
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
        "Get-Process python | Where-Object { $_.MainWindowHandle -ne 0 } | "
        "Select-Object -ExpandProperty MainWindowHandle | Select-Object -First 1"],
        capture_output=True, text=True).stdout.strip()
    return int(out) if out else 0

def capture(hwnd, out):
    user32.SetForegroundWindow(hwnd)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        print("nonzero rect:", w, h); return
    gdi32 = ctypes.windll.gdi32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w; bmi.biHeight = -h; bmi.biPlanes = 1
    bmi.biBitCount = 32; bmi.biCompression = 0
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, 2)  # PW_RENDERFULLCONTENT
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    # 保存为 PNG
    try:
        from PIL import Image
        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
        img.save(out)
        print("saved", out, ok, w, h)
    except Exception as e:
        print("PIL err:", e)
        open(out + ".raw", "wb").write(buf.raw)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)

if __name__ == "__main__":
    hwnd = int(sys.argv[1]) if len(sys.argv) > 1 else find_pet()
    out = sys.argv[2] if len(sys.argv) > 2 else "E:/程序/桌面宠物/xbya-vrm-worktree/_pet_window.png"
    if not hwnd:
        print("no hwnd"); sys.exit(1)
    capture(hwnd, out)
