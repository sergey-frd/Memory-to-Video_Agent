# История неповторяющихся запусков batch-файлов

Этот файл фиксирует уникальные шаблоны запуска `.bat`-файлов. Он нужен, чтобы не держать в голове разные варианты параметров и не плодить одинаковые команды в документации.

Правило для ChatGPT desktop-flow: рабочее окно генерации должно быть отдельным Chrome-окном ChatGPT с одной видимой вкладкой. `run_chatgpt_portrait_batch_existing.bat` теперь добавляет `--desktop-require-single-tab-window`, поэтому при нескольких ChatGPT-окнах скрипт выбирает или требует именно dedicated generation window.

Правило для Grok web-flow: portrait batch использует тот же профиль `.browser-profile\grok-web`, что и Grok video pipeline, но запускает `api/grok_web.py` в image-режиме. Перед первым запуском или после разлогина выполните `login_grok_profile.bat`, проверьте `https://grok.com/imagine`, затем закройте login-окно Chrome.

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

## Рабочая команда для текущей задачи

```bat
.\run_chatgpt_portrait_batch_existing.bat --config-file chatgpt_watercolor_scene_expansion_config.json --skip-existing --continue-on-error --desktop-reactivate-delay 0 --desktop-click-composer
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
