# История неповторяющихся запусков batch-файлов

Этот файл фиксирует уникальные шаблоны запуска `.bat`-файлов. Он нужен, чтобы не держать в голове разные варианты параметров и не плодить одинаковые команды в документации.

Правило для ChatGPT desktop-flow: рабочее окно генерации должно быть отдельным Chrome-окном ChatGPT с одной видимой вкладкой. `run_chatgpt_portrait_batch_existing.bat` теперь добавляет `--desktop-require-single-tab-window`, поэтому при нескольких ChatGPT-окнах скрипт выбирает или требует именно dedicated generation window.

Правило для Grok web-flow: portrait batch использует тот же профиль `.browser-profile\grok-web`, что и Grok video pipeline, но запускает `api/grok_web.py` в image-режиме. Перед первым запуском или после разлогина выполните `login_grok_profile.bat`, проверьте `https://grok.com/imagine`, затем закройте login-окно Chrome.

Правило для Grok API-flow: `run_full_grok_pipeline_api.bat` не использует Chrome и не зависит от `.browser-profile\grok-web`. Перед запуском проверьте, что в `.env` заполнен `XAI_API_KEY`, а быстрый ping выполняется командой `python .\scripts\xai_ping.py`. Веб- и API-варианты используют одинаковую структуру `input/` → `output/` → `final_videos_dir`/`regeneration_assets_dir`, поэтому скрипты доставки и cleanup не меняются.

Правило по длительности видео: `run_full_grok_pipeline.bat` теперь читает `video_duration_seconds` из активного конфига и принудительно нажимает соответствующую кнопку в Grok UI до загрузки изображения и prompt. Скоринг в `_nudge_prompt_submit_controls` ставит запрошенную длительность выше уже выделенной (даже если по умолчанию UI предлагал, например, 6s, а в конфиге стоит 10s).

Правило безопасности ввода: перед кликами, вставкой, Enter и сохранением desktop-агент проверяет, что foreground-окно — выбранный ChatGPT или настоящий диалог `Save As`/`Open`. Если сверху Premiere Pro, Total Commander или другое приложение, batch должен остановиться, а не отправлять туда клавиши.

## Текущие уникальные команды

| ID | Batch-файл | Назначение | Неповторяющийся пример запуска |
| --- | --- | --- | --- |
| B001 | `login_grok_profile.bat` | Ручной вход в Grok automation profile | `.\login_grok_profile.bat` |
| B002 | `login_chatgpt_profile.bat` | Ручной вход в ChatGPT profile для обычной web-автоматизации | `.\login_chatgpt_profile.bat` |
| B003 | `login_chatgpt_debug_profile.bat` | Ручной вход в ChatGPT debug profile с remote debugging port `9333` | `.\login_chatgpt_debug_profile.bat` |
| B004 | `run_full_grok_pipeline.bat` | Полный Grok pipeline по `input` через `config.json` | `.\run_full_grok_pipeline.bat --upload-timeout 300` |
| B005 | `run_full_grok_pipeline_local.bat` | Полный Grok pipeline через локальный `.venv` и `config.local.json` | `.\run_full_grok_pipeline_local.bat --skip-existing --upload-timeout 300` |
| B006 | `run_grok_automation.bat` | Один Grok job для одного изображения и prompt-файла | `.\run_grok_automation.bat --image .\input\photo.jpg --prompt .\output\photo_20260314_101010_v_prompt_1.txt --upload-timeout 300` |
| B007 | `run_grok_automation_all.bat` | Grok batch по уже готовым `*_v_prompt_*.txt` | `.\run_grok_automation_all.bat --skip-existing --upload-timeout 300` |
| B008 | `run_chatgpt_portrait_batch.bat` | ChatGPT portrait batch через стандартный backend/параметры Python | `.\run_chatgpt_portrait_batch.bat --config-file chatgpt_portrait_config.json --skip-existing` |
| B009 | `run_chatgpt_portrait_batch_debug.bat` | ChatGPT portrait batch через debug Chrome на `9333` | `.\run_chatgpt_portrait_batch_debug.bat --config-file chatgpt_portrait_config.json --skip-existing` |
| B010 | `run_chatgpt_portrait_batch_existing.bat` | Рекомендуемый desktop-flow через уже открытое single-tab окно ChatGPT | `.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_portrait_base_config.json --skip-existing --desktop-reactivate-delay 0 --desktop-click-composer` |
| B011 | `run_chatgpt_portrait_batch_existing.bat` | Продолжение только watercolor + SCENE_EXPANSION по `input` | `.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_watercolor_scene_expansion_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer` |
| B012 | `run_local_portrait_batch.bat` | Локальная stylization-проверка без ChatGPT UI | `.\run_local_portrait_batch.bat --config-file chatgpt_portrait_config.json --skip-existing` |
| B013 | `run_openai_portrait_batch.bat` | Portrait/image edit batch через OpenAI Images API | `.\run_openai_portrait_batch.bat --config-file chatgpt_portrait_config.json --skip-existing --api-model gpt-image-1.5` |
| B014 | `run_project_sequence_batch.bat` | Batch-оптимизация Premiere sequence по указанному JSON | `.\run_project_sequence_batch.bat .\project_sequence_batch_igor_26_1A.json` |
| B015 | `run_project_sequence_batch_igor_26_1A.bat` | Готовый Igor sequence batch wrapper | `.\run_project_sequence_batch_igor_26_1A.bat` |
| B016 | `run_project_sequence_batch_nicol_26_T2.bat` | Готовый Nicol sequence batch wrapper | `.\run_project_sequence_batch_nicol_26_T2.bat` |
| B017 | `run_project_sequence_batch_vika_26_1A.bat` | Готовый Vika sequence batch wrapper | `.\run_project_sequence_batch_vika_26_1A.bat` |
| B018 | `run_project_publication_stage.bat` | Подготовить публикационный snapshot без push | `.\run_project_publication_stage.bat --source-root . --dry-run` |
| B019 | `run_project_publication_push.bat` | Подготовить и отправить публикационный snapshot в внешний repo | `.\run_project_publication_push.bat --source-root .` |
| B020 | `login_gemini_profile.bat` | Ручной вход в Gemini profile для отдельного single-tab окна генерации | `.\login_gemini_profile.bat` |
| B021 | `run_gemini_portrait_batch_existing.bat` | Gemini desktop-flow с теми же portrait JSON-конфигами, quiet by default; output-каталоги зеркалятся из `output\chatgpt_*` в `output\gemini_*`, сохранение идет через full-size download button | `.\run_gemini_portrait_batch_existing.bat --config-file chatgpt_portrait_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer` |
| B022 | `run_grok_portrait_batch_existing.bat` | Grok web-flow с теми же portrait JSON-конфигами и профилем `.browser-profile\grok-web`; output-каталоги зеркалятся из `output\chatgpt_*` в `output\grok_*` | `.\run_grok_portrait_batch_existing.bat --config-file chatgpt_portrait_base_config.json --skip-existing --continue-on-error` |
| B023 | `run_full_grok_pipeline_api.bat` | Полный Grok video pipeline через прямой xAI API (`grok-imagine-video`), без Chrome и Playwright; требует `XAI_API_KEY` в `.env` | `.\run_full_grok_pipeline_api.bat --config-file .\config_SF.json` |
| B024 | `run_video_prompt_story_generate.bat` | Сгенерировать reviewable HTML/JSON историю для multi-scene video prompt | `.\run_video_prompt_story_generate.bat` |
| B025 | `run_video_prompt_story_export.bat` | Экспортировать composer JSON после правок HTML-истории | `.\run_video_prompt_story_export.bat <LOCAL_PATH>` |
| B026 | `run_project_publication_push.bat` | Опубликовать полный bundle на GitHub (`Memory-to-Video_Agent`, tag `v2026.06.10.02`) | `.\run_project_publication_push.bat --source-root .` |
| B027 | `run_chatgpt_style_batch_existing.bat` | Все 37 стилей base-банка по `input/` через `chatgpt_all_styles_config.json`; результаты в `output\chatgpt_all_styles` | `run_chatgpt_style_batch_existing.bat chatgpt_all_styles_config.json --skip-existing --continue-on-error` |
| B028 | `run_chatgpt_portrait_batch_existing.bat` | То же «все стили», но через основной desktop-launcher (не laptop style-batch) | `.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_all_styles_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer` |
| B029 | `run_sequence_trim_review.bat` | Premiere Sequence Trim Review: heuristic + semantic KEEP/DROP сегменты, compact keep | `.\run_sequence_trim_review.bat .\sequence_trim_review_01.json` |
| B030 | `run_hero_definition.bat` | Создать `hero_def.json` из эталонных фотографий и `human_detail_txt` | `.\run_hero_definition.bat .\hero_definition_Alice.json` |
| B031 | `run_sequence_trim_review.bat` | Hero-aware HIGH/MEDIUM/REVIEW/DROP анализ по `hero_def.json` | `.\run_sequence_trim_review.bat .\sequence_trim_review_Alice_1.json` |
| B032 | `run_sequence_trim_review.bat` | Повторный экспорт одной sequence с V1 HIGH, V2 MEDIUM, V3 REVIEW, V4 DROP без OpenAI | `.\run_sequence_trim_review.bat .\sequence_trim_review_Alice_replay_levels.json` |
| B033 | `run_sequence_music_recommendation.bat` | Video-only + персонализированный music report из project config, sequence config и `hero_def.json` | `.\run_sequence_music_recommendation.bat .\sequence_music_recommendation_Alice.json` |
| B034 | `run_chatgpt_portrait_batch_existing.bat` | Только психологический русский реалистический портрет `ILYA_REPIN` | `.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_ilya_repin_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer` |
| B035 | `run_chatgpt_portrait_batch_existing.bat` | Подмножество художников: Picasso Blue/Rose, Vermeer, Caravaggio, Rodin, Michelangelo, Matisse, Botticelli, Toulouse-Lautrec, Modigliani | `.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_selected_artists_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer` |
| B036 | `run_chatgpt_portrait_batch_existing.bat` | Русские художники: Серов, Васнецов, Врубель, Левитан | `.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_russian_artists_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer` |
| B037 | `run_sequence_trim_review.bat` | Применить ручной KEEP JSON к копии Premiere-проекта через общий trim-review launcher (`mode=apply_keep_ranges`) | `.\run_sequence_trim_review.bat .\sequence_keep_apply_yotam26_2_min.json` |
| B038 | `run_sequence_keep_apply.bat` | Отдельный launcher для `apply_keep_ranges`: Yotam 2-min KEEP JSON | `.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_2_min.json` |
| B039 | `run_sequence_keep_apply.bat` | Тот же режим по шаблону нового проекта | `.\run_sequence_keep_apply.bat .\sequence_keep_apply_template.json` |
| B040 | `run_sequence_keep_apply.bat` | Второй проход Yotam: KEEP JSON с `project_path`, `sequence_name` и несколькими `keep_ranges` | `.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_2_min_vtr_2.json` |
| B041 | `run_sequence_media_import.bat` | Импорт списка файлов из корневой папки в Premiere sequence | `.\run_sequence_media_import.bat .\sequence_media_import_yotam26_part2.json` |
| B042 | `run_sequence_import_and_keep.bat` | Импорт списка файлов и keep/очистка за один проход | `.\run_sequence_import_and_keep.bat <LOCAL_PATH>` |

## GitHub publication note (2026-08-16)

Dev history: https://github.com/sergey-frd/img-style-ag_1 (`git push origin main`).

Public Internet bundle: https://github.com/sergey-frd/Memory-to-Video_Agent at version **`2026.08.16.01`** (tag `v2026.08.16.01`). Headline: Premiere import / keep-apply / import-and-keep plus expanded portrait style banks.

## Рабочая команда для текущей задачи

Применить ручной KEEP JSON к копии Premiere-проекта Yotam:

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_2_min.json
```

Тот же JSON через общий launcher:

```bat
.\run_sequence_trim_review.bat .\sequence_keep_apply_yotam26_2_min.json
```

Новый проект по шаблону:

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_apply_template.json
```

Ожидаемый результат keep-apply:
- исходный `Yotam26_2_min.prproj` не меняется;
- новый проект: `<LOCAL_PATH>`;
- отчёты: `<LOCAL_PATH>`;
- в консоли: `Keep apply completed successfully.`

Gemini equivalent with the same config format:

```bat
.\run_gemini_portrait_batch_existing.bat --config-file chatgpt_portrait_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer
```

Grok equivalent with the same config format:

```bat
.\run_grok_portrait_batch_existing.bat --config-file chatgpt_portrait_base_config.json --skip-existing --continue-on-error
```

Ожидаемый результат:
- входные изображения берутся из `input`;
- готовые файлы пишутся в `output\chatgpt_watercolor_scene_expansion`;
- для Gemini и Grok без явного `--output-dir` эти же config-папки зеркалятся в `output\gemini_*` и `output\grok_*`;
- имена результатов: `<image_stem>_watercolor.png` и `<image_stem>_scene_expansion.png`;
- при рестарте `--skip-existing` пропускает уже сохраненные изображения.
