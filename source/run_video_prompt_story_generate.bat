@echo off
setlocal
cd /d "%~dp0"
.\.venv\Scripts\python.exe -u .\main_video_prompt_story.py --config-file .\video_prompt_story_config_alex_krvz.json --generate
endlocal
