@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
 echo Usage: run_video_workflow.bat config_workflow_Hero.local.json --action start^|alternative^|best-assembly^|select^|revise^|approve^|finish^|status^|accept
 exit /b 2
)
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
"%PYTHON%" -B -u "%~dp0tools\run_video_workflow.py" --config %*
exit /b %ERRORLEVEL%
