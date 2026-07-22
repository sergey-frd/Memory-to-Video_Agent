@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PROJECT_ROOT=%~dp0."

"%PYTHON_EXE%" -u "%~dp0tools\check_laptop_watercolor_env.py" --project-root "%PROJECT_ROOT%" compare --baseline "%~dp0env_baseline_chatgpt_watercolor.json" --strict-versions --report "%~dp0env_compare_report_chatgpt_watercolor.json" %*
exit /b %ERRORLEVEL%
