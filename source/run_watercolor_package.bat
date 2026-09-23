@echo off
setlocal
chcp 65001 >nul
if "%~1"=="" (
  echo Usage: run_watercolor_package.bat CONFIG [--dry-run]
  exit /b 2
)
set "ART_CONFIG=%~f1"
set "ART_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "ART_PYTHON=%~dp0.venv\Scripts\python.exe"
cd /d "%~dp0"
"%ART_PYTHON%" -B "%~dp0tools\run_watercolor_package.py" --config "%ART_CONFIG%" --env-file "%~dp0.env" %2
exit /b %ERRORLEVEL%
