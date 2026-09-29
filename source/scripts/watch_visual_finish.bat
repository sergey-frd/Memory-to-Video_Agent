@echo off
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch_visual_finish.ps1" -TaskId "%~1"
pause
