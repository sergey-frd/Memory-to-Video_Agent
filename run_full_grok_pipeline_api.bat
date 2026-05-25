@echo off
setlocal

cd /d "%~dp0"

python ".\main_full_pipeline_api.py" --config-file ".\config.json" %*
exit /b %errorlevel%
