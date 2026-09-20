@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
 echo Usage: run_prepare_native_finish.bat config_native_finish_Max26.local.json [--dry-run]
 exit /b 2
)
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
"%PYTHON%" -B -u "%~dp0tools\prepare_native_finish.py" --config "%~1" %2
exit /b %ERRORLEVEL%
