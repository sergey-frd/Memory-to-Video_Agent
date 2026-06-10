@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "PYTHONWARNINGS=ignore::SyntaxWarning"
set "PYTHONIOENCODING=utf-8"

"%PYTHON_EXE%" -u "%~dp0main_chatgpt_portrait_batch.py" --backend desktop --desktop-verbose --desktop-active-window --desktop-require-single-tab-window --desktop-clipboard-attach --desktop-reactivate-delay 5 --desktop-send-cursor-delay 0 --desktop-save-context-menu --desktop-post-attach-delay 8 --desktop-min-result-wait 100 --desktop-result-stable-wait 12 --result-timeout 600 %*
exit /b %ERRORLEVEL%
