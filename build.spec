# build.spec
# -*- mode: python ; coding: utf-8 -*-
# 打包小忆桌面宠物为独立 exe（onedir 模式）
# 使用: pyinstaller build.spec

import os

datas = [
    ('config.yaml', '.'),
    ('resources', 'resources'),  # 宠物素材+manifest
    ('assets', 'assets'),
    ('assets/vrm', 'assets/vrm'),  # VRM 模型 + viewer.html（3D 渲染核心资源）
    ('plugins', 'plugins'),  # 插件目录（运行时按文件动态加载，必须整目录打包）
    ('data', 'data'),  # vectordb / voiceprint 存储目录
]

# 插件通过 importlib 在磁盘上动态加载，PyInstaller 无法静态分析，
# 依赖必须显式声明为 hiddenimports
hiddenimports = [
    'pygame',          # core.app._play_audio 用到
    'pyaudio',         # 麦克风（asr 插件）
    'faster_whisper',  # ASR
    'edge_tts',        # TTS
    'watchfiles',      # 文件监控
    'PySide6.QtWidgets',
    'PySide6.QtGui',
    'PySide6.QtCore',
    # QtWebEngine（PetWindow.enable_vrm lazy import，静态分析捕不到，必须显式声明；
    # PyInstaller 官方 hook 会自动带上 QtWebEngineProcess.exe / resources / translations）
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel',
]

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 瘦身：排除环境无关大库（torch/paddle/numba 等；hardware_detector 有 nvidia-smi
    # fallback，voiceprint 3d_speaker 未接入交互链路，异常时插件系统 NullObject 降级）
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'paddle', 'paddlepaddle',
        'llvmlite', 'numba',
        'opentelemetry', 'sklearn', 'scikit-learn',
        'matplotlib', 'pandas', 'scipy',
        'cv2', 'opencv-python', 'opencv-python-headless',
        'transformers', 'onnxruntime',
        'PIL', 'Pillow',
        'setuptools', 'pkg_resources',
        'PyQt5', 'PyQt6', 'PySide2',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# 图标需 .ico；若提供 .png 且安装了 Pillow 则自动转换，否则忽略图标
icon_path = 'assets/pet/icon.ico' if os.path.exists('assets/pet/icon.ico') else ('assets/pet/cat_base.png' if os.path.exists('assets/pet/cat_base.png') else None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='XiaoYiPet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台便于查看日志
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='XiaoYiPet',
)
