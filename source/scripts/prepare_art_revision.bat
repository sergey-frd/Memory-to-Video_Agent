@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0.."
python -u "%~dp0prepare_art_revision.py" %*
set "art_exit=%ERRORLEVEL%"
popd
exit /b %art_exit%
