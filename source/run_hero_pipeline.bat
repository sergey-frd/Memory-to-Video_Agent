@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
 echo Usage: run_hero_pipeline.bat config_pipeline_Hero.local.json [--check] [--until native]
 exit /b 2
)
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
"%PYTHON%" -B -u "%~dp0tools\run_hero_pipeline.py" --config "%~1" %2 %3 %4
exit /b %ERRORLEVEL%
