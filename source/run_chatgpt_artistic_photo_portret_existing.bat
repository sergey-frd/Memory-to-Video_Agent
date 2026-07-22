@echo off
setlocal

call "%~dp0run_chatgpt_style_batch_existing.bat" "%~dp0chatgpt_artistic_photo_portret_config.json" %*
exit /b %ERRORLEVEL%
