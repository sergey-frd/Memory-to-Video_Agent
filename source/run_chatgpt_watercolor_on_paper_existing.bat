@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "PYTHONWARNINGS=ignore::SyntaxWarning"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

if exist "%~dp0api\__pycache__" (
  del /q "%~dp0api\__pycache__\chatgpt_desktop_v2*.pyc" >nul 2>nul
)

echo Using Python: "%PYTHON_EXE%"
"%PYTHON_EXE%" -c "import sys; print(sys.executable)"

rem Ensure desktop automation dependencies exist in the SAME interpreter used by this bat.
"%PYTHON_EXE%" -c "import pywinauto, pyperclip, PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing desktop dependencies into "%PYTHON_EXE%" ...
  "%PYTHON_EXE%" -m pip install --upgrade pywinauto pyperclip pywin32 pillow
  if errorlevel 1 (
    echo Failed to install desktop dependencies.
    exit /b 1
  )
)

"%PYTHON_EXE%" -u "%~dp0main_chatgpt_portrait_batch.py" --backend desktop --desktop-verbose --desktop-active-window --desktop-new-chat --desktop-new-chat-timeout 25 --desktop-click-composer --desktop-require-single-tab-window --desktop-clipboard-attach --desktop-no-file-dialog-fallback --desktop-save-context-menu --desktop-reactivate-delay 5 --desktop-send-cursor-delay 0 --desktop-post-attach-delay 8 --desktop-min-result-wait 100 --desktop-result-stable-wait 12 --result-timeout 600 --config-file "%~dp0chatgpt_watercolor_on_paper_config.json" --continue-on-error %*
exit /b %ERRORLEVEL%
