@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "JOB_CONFIG=%~1"
if not defined JOB_CONFIG set "JOB_CONFIG=config_revision_Ben26_v2.local.json"
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
"%PYTHON%" -B "%~dp0tools\revise_edit_plan.py" --config "%JOB_CONFIG%"
exit /b %ERRORLEVEL%
