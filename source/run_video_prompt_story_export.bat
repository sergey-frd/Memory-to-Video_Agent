@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_video_prompt_story_export.bat path\to\video_prompt_story_YYYYMMDD_HHMMSS.html
  exit /b 1
)
.\.venv\Scripts\python.exe -u .\main_video_prompt_story.py --config-file .\video_prompt_story_config_alex_krvz.json --export-config --story-html "%~1"
endlocal
