@echo off
setlocal
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_project.ps1" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
popd
exit /b %INSTALL_EXIT%
