@echo off
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch_main_cut.ps1" -TaskId "%~1"
pause
