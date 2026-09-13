@echo off
chcp 936 >nul
title 欣雅 桌面智能管家 - 启动器
color 0E

rem ============================================================
rem  欣雅 桌面智能管家 - 带控制台启动（可看日志）
rem
rem  【本文件必须存为 GBK/ANSI 编码】
rem    cmd.exe 在执行 chcp 之前就已按系统 ANSI 读取整个 .bat，
rem    存成 UTF-8 会让中文被误读成命令（实测把 "if not exist"
rem    读成乱码后直接报错，整个启动器失效）。改这个文件时请注意编码。
rem
rem  【日常请双击「启动欣雅.vbs」，不是本文件】
rem    - 本文件用 python.exe（控制台版）=> **必然弹出黑窗口**，
rem      而且那个窗口一关，程序就跟着退出。
rem    - 「启动欣雅.vbs」用 pythonw.exe + 隐藏窗口 => 无黑窗，日常用它。
rem  保留本文件的意义：启动异常时它能让你**直接看到报错**，
rem  这是无窗口启动做不到的 —— 所以它现在的定位是"排查工具"。
rem
rem  两者都从自身所在目录推导项目根，不写死路径。
rem  （上一版 VBS 写死了 xiaoyi-vrm-worktree，改名后静默失效 —— 已修。）
rem ============================================================

cd /d "%~dp0"

echo.
echo  ==========================================
echo      欣雅 桌面智能管家  v2.0
echo  ==========================================
echo   项目目录: %CD%
echo.

rem ---- 1. 检查启动脚本 ----
if not exist "run.py" (
    echo  [错误] 当前目录下找不到 run.py
    echo         请把本文件放在项目根目录（与 run.py 同级）再运行。
    echo.
    pause
    exit /b 1
)

rem ---- 2. 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo  [错误] 未找到 python，请先安装 Python 3.10+ 并加入 PATH。
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])" 2^>nul') do set PYVER=%%v
echo  [1/3] Python 版本: %PYVER%

rem ---- 3. 检查依赖（缺才装）----
echo  [2/3] 检查依赖...
python -c "import yaml,psutil,PySide6,edge_tts,requests" >nul 2>nul
if errorlevel 1 (
    echo        缺少依赖，正在安装...
    python -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo.
        echo  [错误] 依赖安装失败。请手动执行：
        echo         python -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo        依赖安装完成
) else (
    echo        依赖齐全
)

rem ---- 4. 启动 ----
echo  [3/3] 正在启动...
echo.
echo  ------------------------------------------
echo   提示：右键桌宠可打开菜单；关闭本窗口即退出程序
echo  ------------------------------------------
echo.

python run.py
set RC=%errorlevel%

if not "%RC%"=="0" (
    echo.
    echo  ==========================================
    echo   [错误] 程序退出，代码: %RC%
    echo  ==========================================
    echo.
    pause
    exit /b %RC%
)

echo.
echo  欣雅已退出。
echo.
pause