---
name: grok-video-pipeline
description: Run the full Grok image-to-video generation pipeline for images in `input/`. Covers both backends - web automation (`run_full_grok_pipeline.bat`, Playwright + Chrome profile) and direct xAI API (`run_full_grok_pipeline_api.bat`, `xai-sdk` + `XAI_API_KEY`). Use when the user wants to generate background images and/or videos from photos through Grok, asks about `grok-imagine-video`, `XAI_API_KEY`, `video_duration_seconds`, the `6s/10s/15s` duration button bug, or any `*_v_prompt_*.txt`/`*_video_*.mp4` artifacts.
---

# Grok Video Pipeline

End-to-end pipeline that reads images from `input/`, generates per-image video/background prompts, runs them through Grok, and delivers the final `mp4` plus regeneration assets to the configured project folders.

## Two backends, same input/output layout

| Backend | Launcher | Driver | Auth |
| --- | --- | --- | --- |
| Web automation | `run_full_grok_pipeline.bat` | Playwright + Chrome profile `.browser-profile\grok-web` | Manual Grok login |
| Direct xAI API | `run_full_grok_pipeline_api.bat` | `xai-sdk` Python client, `grok-imagine-video` model | `XAI_API_KEY` in `.env` |

Both reuse the same `input/` → `output/` → `final_videos_dir`/`regeneration_assets_dir` chain. The pipeline code (`main_full_pipeline.py` / `main_full_pipeline_api.py`) only differs in which `AgentRunner` it wires in.

## Decide which backend

- Pick **web** when you need Grok-only UI features, chat refinements, or the xAI Video API quota is unavailable.
- Pick **API** for headless / CI / scriptable runs, deterministic behavior, or when there's no Chrome session to maintain.

## Prerequisites checklist

Copy and walk through this before launching:

```
- [ ] input/ contains the source images (.jpg/.png) you want processed
- [ ] An active config: config.json (default) or a profile config like config_SF.json
- [ ] If web backend: ran login_grok_profile.bat once, verified https://grok.com/imagine, then closed that Chrome window
- [ ] If API backend: .env contains XAI_API_KEY=... (use .env.template as the shape)
- [ ] If API backend: `python .\scripts\xai_ping.py` prints "pong"
- [ ] video_duration_seconds in the chosen config matches what you actually want (6, 10, or 15)
```

## Launch examples

Web backend:

```bat
run_full_grok_pipeline.bat --upload-timeout 300
run_full_grok_pipeline.bat --config-file .\config_SF.json --upload-timeout 300
run_full_grok_pipeline.bat --skip-video --generate-source-background --upload-timeout 300
run_full_grok_pipeline.bat --save-grok-debug-artifacts --upload-timeout 300
```

API backend (no Chrome required):

```bat
run_full_grok_pipeline_api.bat
run_full_grok_pipeline_api.bat --config-file .\config_SF.json
run_full_grok_pipeline_api.bat --skip-video --generate-source-background
```

Common useful flags (both backends): `--config-file <path>`, `--skip-video`, `--skip-existing`, `--generate-source-background`, `--continue-on-error`, `--video-duration-seconds N`.

## Where the output lands

- `output/` - temporary prompt files, manifests, intermediate stage artifacts.
- `final_videos_dir` (from config) - delivered `*_video_*.mp4` and background images.
- `regeneration_assets_dir` (from config) - prompt files and manifests for manual re-runs.
- `error/input` + `error/output` - everything from a failed stage, kept for triage.

## Duration button rule (web backend)

`video_duration_seconds` in the config (or `--video-duration-seconds N` on the CLI) drives the duration button choice on `grok.com/imagine`. The launcher now reads the value, then in `api/grok_web.py`:

- `GrokWebConfig.duration_seconds` holds the requested value.
- `_set_video_duration()` runs before image upload and prompt entry, with retries.
- The JS scoring inside `_nudge_prompt_submit_controls` ranks the requested duration above any pre-selected (wrong) button (`+25000` vs `+10000`).

Acceptable Grok values: `6`, `10`, `15` seconds. If the UI ships with a new value, extend the scoring labels (`{N}s`, `{N}sec`, `{N}seconds`).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Pipeline stalls right after "submit clicked" | A modal/dialog covers the page | Close Chrome processes, re-run `login_grok_profile.bat`, retry with `--save-grok-debug-artifacts` |
| Generated video is the wrong length | `video_duration_seconds` not propagated | Confirm the config value and the CLI; web backend now forces the button — see "Duration button rule" above |
| `XAI_API_KEY is missing from environment / .env` | API key not set or `.env` not loaded | Fill `XAI_API_KEY` in `.env`, re-run `python .\scripts\xai_ping.py` |
| xAI returns `[code]: ...` | xAI Video API error | Read the code+message from `GrokVideoError`; quota/auth/parameter issues raise distinct codes |
| `xAI video generation timed out after Ns` | API run exceeded timeout | Raise `result_timeout_ms` in config or wait for xAI side to settle |
| Web flow fails Grok login screen | Session expired | `login_grok_profile.bat`, sign in, verify `https://grok.com/imagine`, close Chrome, retry |

## Reference files in this project

- `main_full_pipeline.py` / `main_full_pipeline_api.py` - the two entry points.
- `api/grok_web.py` - Playwright automation, `GrokWebConfig`, duration logic.
- `api/grok_video.py` - xAI SDK client (image_url + prompt + duration).
- `api/grok_video_runner.py` - `AgentRunner` adapter for the API path.
- `scripts/xai_ping.py` - cheap `XAI_API_KEY` sanity check.
- `.env.template` - shape of the required environment variables.
- `docs/USER_GUIDE_EN.md` / `docs/USER_GUIDE_RU.md` - long-form operator guide.
