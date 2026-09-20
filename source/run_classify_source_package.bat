@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "JOB_CONFIG=%~1"
if not defined JOB_CONFIG set "JOB_CONFIG=config_classification_Ben26.local.json"
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
"%PYTHON%" -B "%~dp0tools\classify_source_package.py" --config "%JOB_CONFIG%" %2 %3 %4
exit /b %ERRORLEVEL%
