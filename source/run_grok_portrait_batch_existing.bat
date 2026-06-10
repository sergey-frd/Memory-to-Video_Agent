@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "PYTHONWARNINGS=ignore::SyntaxWarning"
set "PYTHONIOENCODING=utf-8"

"%PYTHON_EXE%" -u "%~dp0main_chatgpt_portrait_batch.py" --backend grok --target-url https://grok.com/imagine --profile-dir "%~dp0.browser-profile\grok-web" --result-timeout 600 --upload-timeout 180 %*
