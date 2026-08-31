@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run install_project.bat first.
  popd
  exit /b 1
)
".venv\Scripts\python.exe" main_premiere_art_task.py %*
set "ART_EXIT=%ERRORLEVEL%"
popd
exit /b %ART_EXIT%
