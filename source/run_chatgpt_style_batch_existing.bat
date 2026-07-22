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

set "STYLE_CONFIG=%~1"
set "EXTRA_ARGS="
if "%STYLE_CONFIG%"=="" goto use_default
if "%STYLE_CONFIG:~0,1%"=="-" goto use_default
shift
goto collect_args

:collect_args
if "%~1"=="" goto config_ready
set "EXTRA_ARGS=%EXTRA_ARGS% "%~1""
shift
goto collect_args
goto config_ready

:use_default
set "STYLE_CONFIG=%~dp0chatgpt_artistic_photo_portret_config.json"
set "EXTRA_ARGS=%*"

:config_ready
if not exist "%STYLE_CONFIG%" (
  echo Style config file not found: "%STYLE_CONFIG%"
  echo Usage: %~n0 [path_to_chatgpt_style_config.json] [other args]
  exit /b 1
)

echo Using Python: "%PYTHON_EXE%"
"%PYTHON_EXE%" -c "import sys; print(sys.executable)"
echo Using style config: "%STYLE_CONFIG%"

"%PYTHON_EXE%" -c "import pywinauto, pyperclip, PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing desktop dependencies into "%PYTHON_EXE%" ...
  "%PYTHON_EXE%" -m pip install --upgrade pywinauto pyperclip pywin32 pillow
  if errorlevel 1 (
    echo Failed to install desktop dependencies.
    exit /b 1
  )
)

"%PYTHON_EXE%" -u "%~dp0main_chatgpt_portrait_batch.py" --backend desktop --desktop-verbose --desktop-active-window --desktop-new-chat --desktop-new-chat-timeout 25 --desktop-click-composer --desktop-require-single-tab-window --desktop-clipboard-attach --desktop-no-file-dialog-fallback --desktop-save-context-menu --desktop-reactivate-delay 5 --desktop-send-cursor-delay 0 --desktop-post-attach-delay 8 --desktop-min-result-wait 100 --desktop-result-stable-wait 12 --result-timeout 600 --config-file "%STYLE_CONFIG%" --continue-on-error %EXTRA_ARGS%
exit /b %ERRORLEVEL%
