@echo off
setlocal
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=python"
if exist "%SCRIPT_DIR%..\.venv\Scripts\python.exe" set "PYTHON_EXE=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
if "%~1"=="" (
    echo Usage: scripts\run_ingest.bat TASK_ID [--check]
    exit /b 2
)
"%PYTHON_EXE%" -B -u "%SCRIPT_DIR%ingest.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo STOP. Exit code: %EXIT_CODE%
exit /b %EXIT_CODE%
