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
| B043 | `run_sequence_media_import.bat` | Новая sequence в существующем `.prproj` (`import_to_new_sequence`); compact Yotam macro styles с дублем `source_path` | `.\run_sequence_media_import.bat .\sequence_media_import_yotam26_macro_styles.json` |
| B044 | `run_sequence_keep_apply.bat` | Копия source-sequence и KEEP только на копии (`keep_to_new_sequence`); compact Yotam macro styles | `.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_macro_styles.json` |
| B045 | `copy_sequence_images_sveta_igr_26_2.bat` | Копия только картинок sequence Sveta | `.\copy_sequence_images_sveta_igr_26_2.bat` |
| B046 | `copy_sequence_media_sveta_igr_26_2.bat` | Готовый wrapper копирования медиа sequence Sveta | `.\copy_sequence_media_sveta_igr_26_2.bat` |
| B047 | `install_premiere_transition_panel.bat` | Установить CEP-панель Premiere transitions | `.\install_premiere_transition_panel.bat` |
| B048 | `open_ai_work_window.bat` | Открыть одно reusable Chrome-окно Grok/ChatGPT на 9222 | `.\open_ai_work_window.bat` |
| B049 | `open_ai_work_window_bookmarks_profile.bat` | То же reusable-окно через bookmarks-профиль Chrome | `.\open_ai_work_window_bookmarks_profile.bat` |
| B050 | `open_ai_work_window_user_chrome.bat` | То же reusable-окно через обычный user-профиль Chrome | `.\open_ai_work_window_user_chrome.bat` |
| B051 | `run_chatgpt_artistic_photo_portret_existing.bat` | Laptop-batch стиля artistic photo portrait | `.\run_chatgpt_artistic_photo_portret_existing.bat --delivery-config-file config_Ziggi.json` |
| B052 | `run_chatgpt_pair_batch_existing.bat` | Парные портреты из папок input_pair | `.\run_chatgpt_pair_batch_existing.bat --delivery-config-file .\config_SF.json --skip-existing --continue-on-error` |
| B053 | `run_chatgpt_pair_batch_work_window.bat` | Парные портреты через reusable work-window | `.\run_chatgpt_pair_batch_work_window.bat --delivery-config-file .\config_SF.json --skip-existing --continue-on-error` |
| B054 | `run_chatgpt_portrait_batch_work_window.bat` | Portrait batch ChatGPT через reusable work-window | `.\run_chatgpt_portrait_batch_work_window.bat --config-file chatgpt_portrait_base_config.json --skip-existing` |
| B055 | `run_chatgpt_style_menu_existing.bat` | Интерактивное меню банков chatgpt_*_config.json | `.\run_chatgpt_style_menu_existing.bat` |
| B056 | `run_chatgpt_watercolor_on_paper_existing.bat` | Laptop-batch watercolor on paper | `.\run_chatgpt_watercolor_on_paper_existing.bat --delivery-config-file config_Ziggi.json` |
| B057 | `run_copy_minimal_to_laptop_dir.bat` | Скопировать минимальный laptop-bundle | `.\run_copy_minimal_to_laptop_dir.bat` |
| B058 | `run_copy_sequence_images.bat` | Скопировать картинки одной Premiere sequence (CLI) | `.\run_copy_sequence_images.bat --project <prproj> --sequence <sequence> --dest <image_dir>` |
| B059 | `run_copy_sequence_media_batch.bat` | Скопировать медиа sequence по JSON-конфигу | `.\run_copy_sequence_media_batch.bat .\copy_sequence_media_sveta_igr_26_2.json` |
| B060 | `run_full_grok_pipeline_work_window.bat` | Полный Grok pipeline через reusable debug-окно | `.\run_full_grok_pipeline_work_window.bat --upload-timeout 300` |
| B061 | `run_laptop_env_compare.bat` | Сравнить laptop-окружение с watercolor baseline | `.\run_laptop_env_compare.bat` |
| B062 | `run_laptop_env_snapshot.bat` | Снять snapshot laptop watercolor-окружения | `.\run_laptop_env_snapshot.bat` |
| B063 | `run_premiere_transform_script.bat` | Собрать Premiere transform JSX из sequence-batch конфига | `.\run_premiere_transform_script.bat .\project_sequence_batch_igor_26_1A.json` |
| B064 | `run_premiere_transition_script.bat` | Собрать Premiere transition JSX из sequence-batch конфига | `.\run_premiere_transition_script.bat .\project_sequence_batch_igor_26_1A.json` |
| B065 | `run_sequence_media_import_standalone.bat` | Тот же media import через `main_premiere_import_keep.py` | `.\run_sequence_media_import_standalone.bat .\sequence_media_import_template.json` |
| B066 | `run_sequence_keep_apply_standalone.bat` | Тот же KEEP/apply через `main_premiere_import_keep.py` | `.\run_sequence_keep_apply_standalone.bat .\sequence_keep_apply_template.json` |
| B067 | `run_sequence_import_and_keep_standalone.bat` | Тот же import-and-keep через `main_premiere_import_keep.py` | `.\run_sequence_import_and_keep_standalone.bat .\sequence_import_and_keep_template.json` |
| B068 | `run_premiere_sequence_motion.bat` | Дублировать sequence и применить JSON-driven intrinsic Motion | `.\run_premiere_sequence_motion.bat .\premiere_sequence_motion_template.json --dry-run` |
| B069 | `run_premiere_sequence_motion.bat` | Вставить video-only диапазон из другой in-project sequence и анимировать только статичные кадры | `.\run_premiere_sequence_motion.bat .\premiere_sequence_insert_motion_template.json --dry-run` |

## GitHub publication note (2026-08-26)

Dev history: https://github.com/sergey-frd/img-style-ag_1 (`git push origin main`).

Public Internet bundle: https://github.com/sergey-frd/Memory-to-Video_Agent at version **`2026.08.26.01`** (tag `v2026.08.26.01`). Headline: dedicated Premiere import/KEEP runner, portable standalone BAT launchers, multi-root media lookup, and empty-project donor fixes.

## Рабочая команда для текущей задачи

Импортировать стили в новую sequence того же Yotam `.prproj`, затем KEEP-обрезать копию:

```bat
.\run_sequence_media_import.bat .\sequence_media_import_yotam26_macro_styles.json
.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_macro_styles.json
```

Полный 74-file job (workspace, не в репозитории):

```bat
.\run_sequence_media_import.bat <LOCAL_PATH>
.\run_sequence_keep_apply.bat <LOCAL_PATH>
```

Ожидаемый результат in-place import/keep:
- проект: `<LOCAL_PATH>` (отдельный `*_import.prproj` / `*_keep.prproj` не создаётся);
- sequence `Yt_macro_styles_IMPORT_v01` не меняется после KEEP;
- новая sequence `Yt_macro_styles_KEEP_v01` содержит только KEEP-диапазоны;
- одинаковые имена из разных папок остаются разными `MasterClip` / клипами;
- повторный запуск упирается в `fail_if_sequence_exists` / `fail_if_output_sequence_exists`;
- перед записью закройте Premiere.

Старый режим копии проекта (`apply_keep_ranges`):

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_2_min.json
```

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
