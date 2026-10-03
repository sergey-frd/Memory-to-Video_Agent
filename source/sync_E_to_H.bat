@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\mirror_def_ai.ps1" -Direction E-to-H %*
set "sync_result=%errorlevel%"
pause
exit /b %sync_result%
