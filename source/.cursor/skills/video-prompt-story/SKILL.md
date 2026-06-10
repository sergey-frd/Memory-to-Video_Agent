---
name: video-prompt-story
description: Build a reviewable multi-scene video story in HTML from restored photos and regeneration_assets, export composer JSON, then run Seedance generation. Use when the user asks for video_prompt_story, story HTML preview, birthday tribute montage, @imageN story review, main_video_prompt_story.py, run_video_prompt_story_generate.bat, or exporting video_prompt_config before main_video_prompt_composer.py.
---

# Multi-Scene Video Story Preview

Use this workflow when the user already has restored images under `output/chatgpt_photo_restoration` and stage metadata under `regeneration_assets`, but wants to **review and edit the story in HTML first** before generating Seedance JSON prompts.

## Three-step model

| Step | Tool | Output |
| --- | --- | --- |
| 1. Generate story | `main_video_prompt_story.py --generate` | `video_prompt_story_<ts>.html` + `.json` draft |
| 2. Review / edit | Open HTML in browser, edit fields, click **Обновить черновик** | Updated embedded `#story-draft` JSON |
| 3. Export + compose | `--export-config` then `main_video_prompt_composer.py` | `video_prompt_config_*.json` → `Gen_Video_Seedance_*.json` |

## Prerequisites

```
- [ ] `regeneration_assets_dir` exists and contains stage folders for the chosen source files
- [ ] Restored images exist in `final_output_dir/chatgpt_photo_restoration` (or explicit `restored_images_dir`)
- [ ] `video_prompt_story_config_*.json` lists exactly `image_count` source files (default 7)
- [ ] `scene_count * scene_duration_seconds == total_duration_seconds` (default 5 × 2s = 10s)
- [ ] OpenAI credentials available for story generation and composer runs
```

## Config files

| File | Purpose |
| --- | --- |
| `video_prompt_story_config.py` | Config loader / validation |
| `video_prompt_story_config_alex_krvz.json` | Primary chronology story example |
| `video_prompt_story_config_alex_krvz_alt.json` | Alternative montage story example |
| `generation_config_file` | Optional link to `config_*.json` for paths and `grok_multiscene_prompt_size` |

Important config keys:

- `source_files` — explicit list of restored filenames mapped to `@image1..@imageN`
- `story_brief` — story facts for OpenAI (tone, hero rules, excluded files)
- `story_output_stem` — HTML/JSON filename prefix (`video_prompt_story` or `video_prompt_story_alt`)
- `output_dir` — where HTML, drafts, and exported composer configs are written

## Generate story HTML

```bat
.\run_video_prompt_story_generate.bat
```

Or:

```powershell
.\.venv\Scripts\python.exe -u .\main_video_prompt_story.py `
  --config-file .\video_prompt_story_config_alex_krvz.json `
  --generate
```

Alternative montage:

```powershell
.\.venv\Scripts\python.exe -u .\main_video_prompt_story.py `
  --config-file .\video_prompt_story_config_alex_krvz_alt.json `
  --generate
```

## Review in browser

The HTML shows:

- thumbnails from `chatgpt_photo_restoration` with filename + `@imageN`
- editable `Technical Preamble`
- editable scene descriptions with timing
- embedded `#story-draft` JSON updated by **Обновить черновик**

Story rules commonly used in this project:

- group portraits near a tree = **classmate reunion**, not a family story
- call the subject **герой видео / герой ролика / мужчина**, not personal names like `Sasha`, when the brief forbids names
- put `@imageN` tags inline inside scene text, not only in trailing parentheses

## Export composer JSON

```bat
.\run_video_prompt_story_export.bat path\to\video_prompt_story_YYYYMMDD_HHMMSS.html
```

Or:

```powershell
.\.venv\Scripts\python.exe -u .\main_video_prompt_story.py `
  --config-file .\video_prompt_story_config_alex_krvz.json `
  --export-config `
  --story-json "<LOCAL_PATH>" `
  --output-config "<LOCAL_PATH>"
```

Export one composer JSON per story branch (primary / alternative).

## Run Seedance composer

Set `max_prompt_chars` to **2500** in the exported composer JSON if Seedance validation fails at 2000.

```powershell
.\.venv\Scripts\python.exe -u .\main_video_prompt_composer.py `
  --config-file "<LOCAL_PATH>"
```

If `Variant_2` fails on distant-viewpoint validation, rerun with a config that contains only `Variant_2` and add this instruction:

`Keep medium or full shots only; do not use bird's-eye, drone, aerial, or distant viewpoints.`

Composer outputs per variant:

- `Gen_Video_Seedance_Variant_1_<ts>.json` — production EN prompt
- `Gen_Video_Seedance_RU_Variant_1_<ts>.json` — RU control JSON

## Scale to 15 seconds

In the story config, change:

```json
"scene_duration_seconds": 3,
"total_duration_seconds": 15
```

Regenerate HTML, re-export composer JSON, rerun composer.

## Reference

- `main_video_prompt_story.py` — CLI entry point
- `utils/video_prompt_story.py` — HTML render/parse, image discovery, export
- `api/openai_video_prompt_story.py` — OpenAI story synthesis
- `main_video_prompt_composer.py` — Seedance JSON generation
- `test/test_video_prompt_story.py` — unit tests
