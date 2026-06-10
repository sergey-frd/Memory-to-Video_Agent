@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"

set "PYTHON_EXE=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"

"%PYTHON_EXE%" "%SCRIPT_DIR%main_copy_sequence_images.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Image copy failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Image copy completed successfully.
exit /b 0
