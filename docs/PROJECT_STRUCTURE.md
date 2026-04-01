# Структура проекта и контроль изменений

Этот документ нужен в двух ролях:

1. Человеку-разработчику: быстро понять устройство проекта и не пропустить связанный код при изменении.
2. Системе сопровождения: иметь явную карту подсистем, инвариантов и обязательных проверок.

## 1. Архитектурная идея проекта

Проект устроен как набор связанных пайплайнов вокруг одного базового объекта: `stage_id`.

- Источник: входное изображение из `input/` или явно переданный файл.
- Анализ: локальный анализ изображения плюс scene-analysis через OpenAI.
- Синтез: описание сцены, video prompt, background prompt, final-frame prompt, music prompt.
- Исполнение: Grok Web и/или OpenAI Images API.
- Доставка: копирование результатов в `final_videos_dir` и `regeneration_assets_dir`.
- Пост-обработка: sequence optimization, reports, export в XML/PRPROJ.

Практически весь проект держится на четырех контрактах:

1. `GenerationConfig` в `config.py` является единой точкой правды для generation-флагов.
2. Все артефакты одного этапа имеют общий префикс `stage_id`.
3. `output/` служит рабочей временной зоной, а итоговая доставка делается через `utils/project_delivery.py`.
4. Sequence-утилиты используют stage-based артефакты из `regeneration_assets_dir`.

## 2. Карта подсистем

| Подсистема | Основные файлы | Ответственность | Основные выходы |
| --- | --- | --- | --- |
| Конфигурация и пути | `config.py`, `config.json`, `config_BASE.json`, `config_*.json` | Описание флагов, валидация, canonical paths | `GenerationConfig`, `Settings` |
| Анализ изображения и сцены | `utils/image_analysis.py`, `api/openai_scene.py`, `models/scene_analysis.py`, `main_scene.py` | Извлечение визуальных признаков и scene payload | `*_scene_analysis.json`, scene summary |
| Синтез prompts | `utils/prompt_builder.py`, `api/openai_prompt_synthesizer.py`, `api/openai_motion_selector.py`, `utils/camera_movements.py` | Формирование video/background/final-frame/music prompts и motion selection | `*_v_prompt_*.txt`, `*_bg_prompt.txt`, `*_assoc_bg_prompt.txt`, `*_final_frame_prompt_*.txt`, `*_m_prompt.txt` |
| Основной generation pipeline | `main.py` | Склеивает image analysis, scene analysis, motion, prompt generation, optional final frames | полный набор stage-артефактов в `output/` |
| API final-frame pipeline | `main_desktop_pipeline.py` | Многокадровый pipeline с manifest и синхронизацией не-видео артефактов | `*_api_pipeline_manifest.json`, final-frame outputs |
| Grok single-stage runtime | `api/grok_web.py`, `main_grok_web.py` | Генерация background image и/или video для одной prompt-пары | `*_bg_image_16x9.png`, `*_video_*.mp4` |
| Grok batch runtime | `main_grok_batch.py` | Пакетный запуск Grok по всем `*_v_prompt_*.txt` | набор видео и bg-изображений по всем stage |
| Полный sequential pipeline | `main_full_pipeline.py` | Генерация prompts и немедленный Grok-run по каждому входному изображению | доставленные stage outputs, возможное очищение `input/` и `output/` |
| Delivery и lifecycle | `utils/project_delivery.py`, `utils/artifact_cleanup.py`, `main_cleanup_artifacts.py` | Доставка итогов, очистка, перенос ошибок, архивирование | `final_videos_dir`, `regeneration_assets_dir`, `error/`, cleanup reports |
| Sequence optimization | `main_sequence_optimizer.py`, `utils/sequence_optimizer.py`, `utils/sequence_optimizer_runtime.py`, `utils/premiere_xml.py`, `utils/premiere_project.py`, `utils/premiere_xml_export.py`, `utils/premiere_project_export.py`, `models/video_sequence.py` | Анализ монтажной последовательности и выдача рекомендованного порядка | optimized JSON/TXT/XML/PRPROJ |
| Sequence reports и batch orchestration | `main_project_sequence_batch.py`, `main_sequence_reports.py`, `main_human_sequence_report.py`, `utils/project_sequence_batch.py`, `utils/current_sequence_reports.py`, `utils/human_profile_sequence_report.py`, `utils/sequence_structure_report.py`, `utils/transition_recommendations.py`, `utils/fcp_translation_results.py` | Построение отчетов, batch-доставка, human-profile overlays, transition recommendations | reports, batch summaries, transition reports |
| Desktop/web automation | `main_desktop.py`, `api/chatgpt_desktop.py`, `api/chatgpt_desktop_v2.py`, `api/chatgpt_web.py` | Автоматизация desktop/web-взаимодействия для prompt-driven задач | отправка prompts во внешние UI |

## 3. Главные потоки данных

### 3.1 Prompt-generation поток

1. `main.py` принимает `--image` или читает список файлов из `input/`.
2. `GenerationConfig` загружается из JSON и CLI overrides.
3. `utils/image_analysis.py` строит `ImageMetadata`.
4. `api/openai_scene.py` строит `SceneAnalysis`.
5. `api/openai_prompt_synthesizer.py` и `utils/prompt_builder.py` создают prompts.
6. `main.py` пишет stage-файлы в `output/`.
7. `utils/project_delivery.py` синхронизирует non-video артефакты в `regeneration_assets_dir`.

### 3.2 Grok-runtime поток

1. `main_grok_web.py` берет исходное изображение и `*_v_prompt_*.txt`.
2. При включенном `generate_source_background` сначала строится background image.
3. Затем Grok генерирует видео.
4. `utils/project_delivery.py` копирует медиа в `final_videos_dir`.
5. Batch-оболочка `main_grok_batch.py` повторяет это по всем prompt-файлам.

### 3.3 Full pipeline поток

1. `main_full_pipeline.py` последовательно обрабатывает все изображения.
2. Для каждого изображения вызывает `_run_generation()` из `main.py`.
3. Затем запускает Grok через `main_grok_batch.py`.
4. После успеха может удалять обработанное изображение из `input/`.
5. После стадии очищает `output/`.

Это один из самых чувствительных маршрутов проекта: любое изменение lifecycle-поведения здесь влияет на безопасность данных.

### 3.4 Sequence-optimization поток

1. `main_sequence_optimizer.py` читает XML или PRPROJ.
2. Парсеры извлекают клипы и сопоставляют их со stage-артефактами в `regeneration_assets_dir`.
3. `utils/sequence_optimizer.py` вычисляет новый порядок.
4. Экспортеры создают JSON/TXT-отчет и при необходимости новый XML/PRPROJ.
5. Reporting-утилиты строят human-readable overlays и structure reports.

## 4. Критические инварианты проекта

### 4.1 Конфигурационные инварианты

- Все новые generation-флаги должны быть добавлены в `config.py`:
  - в `CONFIG_BOOL_FIELDS` или другой соответствующий набор;
  - в `GenerationConfig`;
  - в `from_dict()`;
  - в `override()`;
  - в документацию;
  - в тесты.
- Одновременно можно включать только один framing-режим:
  - `prefer_face_closeups`
  - `use_ai_optimal_framing`
  - `generate_dual_framing_videos`

### 4.2 Артефактные инварианты

- Stage-артефакты должны иметь единый префикс `stage_id`.
- Sequence optimizer, batch tools и delivery-функции опираются на соглашения по именованию файлов.
- Любое изменение формата имени файла требует проверки всех мест, которые:
  - выводят имя;
  - читают имя обратно;
  - извлекают `stage_id`;
  - синхронизируют артефакты;
  - строят reports.

### 4.3 Lifecycle-инварианты

- `main_grok_batch.py` по умолчанию очищает `input/` и `output/` после успешного batch-run, если не задан `--keep-workdirs`.
- `main_full_pipeline.py` может удалять уже обработанные изображения из `input/`.
- Любое изменение поведения очистки должно рассматриваться как high-risk change.

### 4.4 Prompt-инварианты

- Если меняется смысл prompt-флага, он должен быть согласован:
  - в OpenAI synthesizer;
  - в локальном `PromptBuilder`;
  - в manifest/config docs;
  - в tests.
- Если проект пишет и английские, и русские prompt-файлы, изменение semantics не должно оставаться только в одной языковой ветке без осознанного решения.

### 4.5 Delivery-инварианты

- `.mp4` доставляются в `final_videos_dir`.
- Non-video stage assets доставляются в `regeneration_assets_dir/<stage_id>/`.
- Фоновые изображения обрабатываются отдельно и не должны ломать non-video sync contract.

## 5. Что проверять при каждом типе изменений

### 5.1 Если меняется generation-флаг

Обязательно проверить:

- `config.py`
- `config.json`
- `config_BASE.json`
- профильные `config_*.json`, если флаг нужен в реальных сценариях
- `main.py`
- `main_desktop_pipeline.py`
- `main_full_pipeline.py`, если флаг влияет на полный pipeline
- `api/openai_prompt_synthesizer.py`
- `utils/prompt_builder.py`
- `USER_GUIDE.md`
- `Руководство_пользователя.md`
- профильные тесты

Минимальная проверка:

- загрузка config;
- CLI override;
- manifest serialization;
- prompt output;
- targeted tests.

### 5.2 Если меняется scene-analysis schema

Обязательно проверить:

- `models/scene_analysis.py`
- `api/openai_scene.py`
- `main_scene.py`
- `main.py`
- `api/openai_prompt_synthesizer.py`
- `utils/prompt_builder.py`
- все отчеты и sequence-утилиты, которые читают scene payload из `regeneration_assets_dir`

Минимальная проверка:

- сохранение `*_scene_analysis.json`;
- чтение старых payloads, если нужна обратная совместимость;
- tests для parse и prompt integration.

### 5.3 Если меняется naming или `stage_id`

Обязательно проверить:

- `main.py`
- `main_desktop_pipeline.py`
- `main_grok_web.py`
- `main_grok_batch.py`
- `main_full_pipeline.py`
- `utils/project_delivery.py`
- `utils/premiere_project.py`
- `utils/premiere_xml.py`
- `utils/fcp_translation_results.py`
- sequence reports

Это один из самых широких impact-area проекта.

### 5.4 Если меняется Grok automation

Обязательно проверить:

- `api/grok_web.py`
- `main_grok_web.py`
- `main_grok_batch.py`
- `main_full_pipeline.py`
- связанные timeout/options в конфиге и CLI

Минимальная проверка:

- single-stage run;
- batch run;
- background-only run;
- сценарий `--no-submit`.

### 5.5 Если меняется delivery или очистка

Обязательно проверить:

- `utils/project_delivery.py`
- `main_grok_batch.py`
- `main_full_pipeline.py`
- `utils/artifact_cleanup.py`
- `main_cleanup_artifacts.py`

Минимальная проверка:

- копирование итоговых файлов;
- синхронизация `regeneration_assets_dir`;
- поведение `error/`;
- отсутствие неожиданного удаления `input/`.

### 5.6 Если меняется sequence optimization

Обязательно проверить:

- `models/video_sequence.py`
- `utils/sequence_optimizer.py`
- `utils/sequence_optimizer_runtime.py`
- `utils/premiere_xml.py`
- `utils/premiere_project.py`
- `utils/premiere_xml_export.py`
- `utils/premiere_project_export.py`
- `utils/sequence_structure_report.py`
- `utils/transition_recommendations.py`
- `main_sequence_optimizer.py`

Минимальная проверка:

- JSON/TXT output;
- XML/PRPROJ export, если менялся export path;
- tests по optimizer и reports.

## 6. Обязательный протокол change-control

Эта последовательность должна выполняться и человеком, и автоматической системой.

1. Классифицировать изменение: config, prompt, scene schema, naming, Grok runtime, delivery, optimizer, reports.
2. Открыть `project_structure_registry.json` и выбрать соответствующий `change_type`.
3. Проверить все файлы из `must_touch` и `must_review`, даже если прямое изменение кажется локальным.
4. Обновить документы, если меняется поведение, доступное пользователю или оператору.
5. Обновить tests или добавить новый тест на измененный контракт.
6. Выполнить минимум один targeted test на измененную область.
7. Для high-risk изменений прогнать интеграционный маршрут до артефакта.

## 7. High-risk зоны

Особенно осторожно надо менять:

- `config.py`: ломает все generation-пайплайны сразу;
- `main.py`: центральная точка сборки prompt-артефактов;
- `main_grok_web.py` и `api/grok_web.py`: влияют на реальное выполнение;
- `main_full_pipeline.py`: может менять lifecycle входных файлов;
- `utils/project_delivery.py`: влияет на сохранность и доставку результатов;
- naming contracts и `stage_id`;
- `models/scene_analysis.py` и `models/video_sequence.py`: это data contracts для нескольких подсистем.

## 8. Минимальный набор тестов по классам изменений

### 8.1 Prompt/config изменения

- `test\test_config_cli_defaults.py`
- `test\test_scene_pipeline_integration.py`
- `test\test_motion_selection.py`
- `test\test_video_framing_modes.py`
- `tests\test_selfie_phone_prompt.py`

### 8.2 Grok pipeline изменения

- `test\test_grok_web_app.py`
- `test\test_grok_batch_app.py`
- `test\test_full_pipeline.py`
- `test\test_project_delivery.py`

### 8.3 Scene-analysis изменения

- `test\test_openai_scene.py`
- `test\test_scene_app.py`
- `test\test_scene_pipeline_integration.py`

### 8.4 Sequence/reporting изменения

- `test\test_sequence_optimizer_app.py`
- `test\test_project_delivery.py`

## 9. Машинно-читаемый реестр

Для системы сопровождения рядом лежит файл `project_structure_registry.json`.

Его назначение:

- хранить подсистемы в структурированном виде;
- задавать `change_types`;
- перечислять обязательные точки проверки;
- подсказывать минимальные тесты по каждому классу изменений.

Если при следующем изменении возникает вопрос "что еще может быть затронуто?", первым источником должна быть связка:

- `PROJECT_STRUCTURE.md`
- `project_structure_registry.json`

Для быстрого impact-анализа используйте:

```powershell
python .\main_change_impact.py --change-type generation_flag --changed-file config.py --changed-file utils\prompt_builder.py
```

Если тип изменения заранее неизвестен, можно дать только список файлов:

```powershell
python .\main_change_impact.py --changed-file main_grok_web.py
```

Для интеграции с автоматикой есть JSON-режим:

```powershell
python .\main_change_impact.py --change-type grok_runtime --changed-file main_grok_web.py --json
```

## 10. External Publication Sync

Для отдельного репозитория с живой документацией проекта используйте:

```powershell
python .\main_project_publication.py --target-dir .\project_publication\Memory-to-Video_Agent
```

Если у вас есть локальный клон внешнего репозитория, можно направить обновление прямо туда:

```powershell
python .\main_project_publication.py --target-dir <path-to-local-Memory-to-Video_Agent-clone>
```

Инструмент обновляет управляемый набор файлов:

- `README.md`
- `.gitignore`
- `PUBLISHING.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/CHANGE_IMPACT.md`
- `docs/PROJECT_STRUCTURE.md`
- `docs/USER_GUIDE_EN.md`
- `docs/USER_GUIDE_RU.md`
- `data/project_snapshot.json`
- `data/project_structure_registry.json`
- `data/publication_manifest.json`

Для безопасного stage/commit/push в публичный локальный клон используйте:

```powershell
python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --stage
python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --commit-message "Update project publication" --push
```

Короткая команда для вашего текущего локального клона:

```powershell
.\run_project_publication_push.bat
```

Этот flow:

- проверяет, что target является git-репозиторием;
- проверяет remote `origin` против `Memory-to-Video_Agent`;
- обновляет только managed publication bundle;
- удаляет только stale managed files из предыдущего manifest;
- stage-ит только managed files, а не весь рабочий проект;
- не позволяет пушить новые staged publication changes без явного `--commit-message`.

