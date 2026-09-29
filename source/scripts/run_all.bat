@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0.."
python -u "%~dp0run_all.py" %*
set "pipeline_exit=%ERRORLEVEL%"
popd
echo Pipeline exit code: %pipeline_exit%
exit /b %pipeline_exit%
