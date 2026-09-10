@echo off
chcp 65001 >nul
title Xinya - Desktop Assistant
color 0E

echo.
echo  ==================================
echo      Xinya Desktop Assistant
echo  ==================================
echo.

cd /d "%~dp0"

echo [1/2] Checking dependencies...
python -c "import yaml,psutil,PySide6,edge_tts,requests,faster_whisper" 2>nul
if errorlevel 1 (
    echo [Installing] Missing dependencies...
    pip install -r requirements.txt -q
)

echo.
echo [2/2] Starting Xinya...
echo.
python run.py

if errorlevel 1 (
    echo.
    echo [Error] Start failed, code: %errorlevel%
    pause
)
