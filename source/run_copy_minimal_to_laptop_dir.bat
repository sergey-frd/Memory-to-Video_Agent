@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "TARGET_DIR=<LOCAL_PATH>"

"%PYTHON_EXE%" -u "%~dp0tools\copy_minimal_laptop_bundle.py" --source "%~dp0." --target "%TARGET_DIR%" --run-compare --strict-versions %*
exit /b %ERRORLEVEL%
