@echo off
setlocal
chcp 65001 >nul
if "%~1"=="" (
  echo Usage: run_integrate_art.bat CONFIG [--dry-run] [--retry-id ART_ID]
  exit /b 2
)
set "ART_CONFIG=%~f1"
set "ART_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "ART_PYTHON=%~dp0.venv\Scripts\python.exe"
cd /d "%~dp0"
"%ART_PYTHON%" -B "%~dp0tools\integrate_art.py" --config "%ART_CONFIG%" %2 %3 %4
exit /b %ERRORLEVEL%
