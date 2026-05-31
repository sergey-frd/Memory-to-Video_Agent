@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    set "CONFIG_PATH=%SCRIPT_DIR%copy_sequence_media_sveta_igr_26_2.json"
)

set "PYTHON_EXE=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"

"%PYTHON_EXE%" "%SCRIPT_DIR%main_copy_sequence_media_batch.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Media copy failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Media copy completed successfully.
exit /b 0
