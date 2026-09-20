@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "JOB_CONFIG=%~1"
if not defined JOB_CONFIG set "JOB_CONFIG=config_draft_Ben26.local.json"
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
"%PYTHON%" -B "%~dp0tools\render_structure_draft.py" --config "%JOB_CONFIG%" %2
exit /b %ERRORLEVEL%
