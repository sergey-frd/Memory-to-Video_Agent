@echo off
setlocal
chcp 65001 >nul
if "%~1"=="" (
  echo Usage: run_short_portrait.bat CONFIG [--check]
  exit /b 2
)
set "PORTRAIT_CONFIG=%~f1"
set "PORTRAIT_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PORTRAIT_PYTHON=%~dp0.venv\Scripts\python.exe"
cd /d "%~dp0"
"%PORTRAIT_PYTHON%" -u -B "%~dp0tools\run_short_portrait.py" --config "%PORTRAIT_CONFIG%" %2
exit /b %ERRORLEVEL%
