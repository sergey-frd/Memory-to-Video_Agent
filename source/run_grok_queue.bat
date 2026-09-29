@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
python -X utf8 scripts\run_grok_queue.py %*
set "QUEUE_EXIT=%errorlevel%"
if not "%QUEUE_EXIT%"=="0" echo Queue stopped. See the error above; rerun the same command to resume.
exit /b %QUEUE_EXIT%
