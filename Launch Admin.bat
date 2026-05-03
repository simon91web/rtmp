@echo off
title RTMP Admin
cd /d "%~dp0"
python admin.py
if errorlevel 1 (
    echo.
    echo Something went wrong. Press any key to close.
    pause >nul
)
