---
name: chatgpt-portrait-batch
description: Run the artistic portrait, pair-portrait, or image-edit batch through ChatGPT desktop, Gemini desktop, Grok web, OpenAI API, or local stylizer. Use when the user wants to generate stylized portraits from photos in `input/` or pair portraits from `input_pair/`, asks about `run_chatgpt_portrait_batch_existing.bat`, `run_chatgpt_pair_batch_existing.bat`, `run_gemini_portrait_batch_existing.bat`, `run_grok_portrait_batch_existing.bat`, `run_openai_portrait_batch.bat`, `run_local_portrait_batch.bat`, or any `chatgpt_*_config.json` / `chatgpt_pair_base_config.json` style bank.
---

# ChatGPT / Gemini / Grok Portrait Batch

Generates stylized portraits (Rembrandt, Renaissance, Watercolor, Van Gogh, Klimt, Art Deco, Pop Art, Cubist, Chagall, etc.) plus service styles (`MODERN_COLOR`, `COLORIZE`, `FACE_ENLARGEMENT`, `SCENE_EXPANSION`) by sending each image in `input/` (or each numbered folder in `input_pair/`) through the user's existing ChatGPT/Gemini/Grok session, or through API/local backends.

## Pick a backend

| Backend | Launcher | Uses |
| --- | --- | --- |
| ChatGPT desktop (recommended) | `run_chatgpt_portrait_batch_existing.bat` | Single-tab ChatGPT Chrome window already signed in |
| ChatGPT debug | `run_chatgpt_portrait_batch_debug.bat` | Chrome with `--remote-debugging-port=9333` |
| Pair portraits (ChatGPT) | `run_chatgpt_pair_batch_existing.bat` | `input_pair/<id>/photoA.jpg` + `photoB.jpg` |
| Gemini desktop | `run_gemini_portrait_batch_existing.bat` | Profile via `login_gemini_profile.bat` |
| Grok web (image mode) | `run_grok_portrait_batch_existing.bat` | Profile `.browser-profile\grok-web` |
| OpenAI Images API | `run_openai_portrait_batch.bat` | `OPENAI_API_KEY` in `.env`, `--api-model gpt-image-1.5` |
| Local stylizer | `run_local_portrait_batch.bat` | No external UI/API |

## Prerequisites

```
- [ ] Source images: input/ for portraits, input_pair/<id>/ for pair portraits
- [ ] A style config picked: chatgpt_portrait_config.json (short), chatgpt_portrait_base_config.json (full bank), chatgpt_all_styles_config.json (full bank batch into output/chatgpt_all_styles), chatgpt_pair_base_config.json (pair bank), or one of the special configs (watercolor_scene_expansion, picasso_graphic, etc.)
- [ ] For desktop flows: ONE Chrome window with the target service open and signed in. ChatGPT must be the only ChatGPT window (the launcher enforces `--desktop-require-single-tab-window`).
- [ ] Foreground safety: no Premiere Pro / Total Commander / random app on top of the target window when the batch starts.
- [ ] For OpenAI API path: `.env` has OPENAI_API_KEY.
```

## Recommended commands

Full base style bank through ChatGPT:

```bat
.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_portrait_base_config.json --skip-existing --desktop-reactivate-delay 0 --desktop-click-composer
```

All base styles into `output/chatgpt_all_styles` (trim `portrait_styles` in the config for subsets):

```bat
.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_all_styles_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer
```

Laptop bundle shortcut:

```bat
run_chatgpt_style_batch_existing.bat chatgpt_all_styles_config.json --skip-existing
```

Watercolor + scene expansion only:

```bat
.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_watercolor_scene_expansion_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer
```

Pair portrait into a delivery project:

```bat
.\run_chatgpt_pair_batch_existing.bat --delivery-config-file .\config_SF.json --skip-existing --continue-on-error
```

Gemini equivalent (uses the same JSON configs, mirrors output folders to `output/gemini_*`):

```bat
.\login_gemini_profile.bat
.\run_gemini_portrait_batch_existing.bat --config-file chatgpt_portrait_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer
```

Grok web equivalent (mirrors to `output/grok_*`):

```bat
.\login_grok_profile.bat
.\run_grok_portrait_batch_existing.bat --config-file chatgpt_portrait_base_config.json --skip-existing --continue-on-error
```

Persistent delivery to a user project: add `--delivery-config-file .\config_<Name>.json` so each PNG is mirrored to that config's `final_output_dir` with the same `output/...` subfolder layout preserved.

Large images / slow restoration: pass `--timeout 900` (alias for `--result-timeout`) or set `"result_timeout": 900` in the portrait config JSON. CLI wins over config.

## Output layout

- `output/chatgpt_*` - portraits from ChatGPT.
- `output/gemini_*` - same job names, Gemini variant.
- `output/grok_*` - same job names, Grok variant.
- `output/pair/<id>_art_pair_<YYYYMMDD_HHMMSS>.png` - pair portraits.
- `output/pair/_pair_references/` - one temporary side-by-side reference image per pair (input to the desktop composer).
- `<delivery-config>/final_output_dir/output/...` - mirrored copies when `--delivery-config-file` is passed.

Naming pattern: `<image_stem>_<style_slug>.png`, e.g. `IMG-001_rembrandt.png`. `--skip-existing` makes restarts safe.

## Common pitfalls

| Symptom | Fix |
| --- | --- |
| Batch typing into the wrong app | Foreground safety blocked the keystroke. Bring ChatGPT/Gemini/Grok window to front before resuming. |
| "Multiple ChatGPT windows found" | Close extras or open the dedicated single-tab generation window. |
| Gemini save dialog opens instead of full-size download | Older fallback. The Gemini path prefers `Download full size` / `Скачать в полном размере` button. Update if the UI label changes. |
| Grok stuck on the upload step | Re-run `login_grok_profile.bat`, verify `https://grok.com/imagine`, retry with `--continue-on-error`. |
| Batch moves to the next image before generation finishes | Raise `--timeout` / `--result-timeout` for large images, e.g. `--timeout 1200`. `run_chatgpt_portrait_batch_existing.bat` defaults to `600`; restoration configs can set `"result_timeout": 900` in the portrait JSON. |
| `Accepted result image is no longer visible inside the ChatGPT window` | Usually a false early accept while ChatGPT is still generating, not a short timeout. Keep the dedicated single-tab ChatGPT window in front; rerun with `--timeout 1200`. Desktop automation now waits for the Stop/Стоп control to clear and falls back to direct capture if the context-menu save loses the image handle. |
| Pair batch finds 0 folders | Put **two** images per `input_pair/<id>/`. Supported: `.png`, `.jpg`, `.jpeg`, `.jfif`, `.webp`, `.bmp`, `.tif`, `.tiff`. Names are free (not only `photoA.jpg`); first two sorted files are used. |

## Reference

- `main_chatgpt_portrait_batch.py` - job builder, backend selector.
- `api/chatgpt_desktop_v2.py` - desktop automation for the existing ChatGPT window.
- `api/gemini_desktop.py` - Gemini desktop adapter.
- `api/grok_web.py` - Grok web adapter (image mode).
- `docs/BATCH_RUN_HISTORY.md` - non-repeating example commands per launcher.
- `styles/art_styles_Prompt_list.txt` - source style prompt bank.
