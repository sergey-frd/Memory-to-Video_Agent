# Матрица Parameter → Program → Batch

Этот справочник связывает параметры конфигурации с программами, batch-файлами и результатами. Он охватывает полную цепочку работы с героем и Premiere:

1. создание визуального определения героя;
2. KEEP/DROP-анализ исходной sequence;
3. повторный экспорт из готового отчёта без OpenAI;
4. оптимизацию sequence;
5. персонализированные и музыкальные отчёты.

## Главное различие между описаниями героя

`human_detail_txt` и `hero_def.json` нельзя считать взаимозаменяемыми:

```mermaid
flowchart LR
  T["human_detail_txt\nтекст о человеке"] --> HD["main_hero_definition.py"]
  I["hero_image_dir\nэталонные фотографии"] --> HD
  HD --> J["hero_def.json\nвизуальная идентичность"]
  J --> TR["main_sequence_trim_review.py\nengine=hero"]
  TR --> KD["HIGH / MEDIUM / REVIEW / DROP"]

  T --> PB["main_project_sequence_batch.py"]
  PB --> HR["human_profile_report.txt"]
  HR --> MR["рекомендации по образу героя,\nтону монтажа и музыке"]
```

- `human_detail_txt` содержит биографический и характерологический контекст. В `main_project_sequence_batch.py` он используется при `generate_personalized_report=true` для корректировки образа героя и музыкальных рекомендаций.
- `hero_def.json` создаётся из `human_detail_txt` и эталонных изображений. В `main_sequence_trim_review.py` он используется движком `hero` для визуального поиска героя в грязном видеоматериале.
- Музыкальный отчёт сейчас не читает `hero_def.json` напрямую. KEEP/DROP-анализ не должен использовать невидимые биографические факты как доказательство личности.

## Карта запуска

| Задача | Python-программа | Batch-файл | Основной конфиг | Главный результат |
|---|---|---|---|---|
| Создать визуальное описание героя | `main_hero_definition.py` | `run_hero_definition.bat` | `hero_definition_*.json` | `hero_def.json` |
| Разбить sequence на KEEP/DROP | `main_sequence_trim_review.py` | `run_sequence_trim_review.bat` | `sequence_trim_review_*.json` | review `.prproj` + JSON/TXT |
| Переэкспортировать уровни без OpenAI | `main_sequence_trim_review.py`, режим `report_replay` | `run_sequence_trim_review.bat` | `sequence_trim_review_*_replay_levels.json` | одна sequence, V1–V4 |
| Оптимизировать sequence и построить bundle отчётов | `main_project_sequence_batch.py` | `run_project_sequence_batch.bat`; проектные `run_project_sequence_batch_*.bat` | `project_sequence_batch_*.json` | оптимизированный `.prproj`, отчёты, JSX |
| Построить персонализированный отчёт отдельно | `main_human_sequence_report.py` | нет, прямой Python-запуск | CLI | `*_human_profile_report.txt` |
| Перестроить отчёты после ручного монтажа | `main_sequence_reports.py` | нет, прямой Python-запуск | CLI | current-order JSON, music/structure/transition TXT |
| Построить music-first отчёт напрямую из Premiere | `main_sequence_music_first.py` | нет, прямой Python-запуск | CLI | JSON + music TXT, опционально structure/transition |

## 1. Hero Definition

Пример запуска:

```bat
run_hero_definition.bat hero_definition_Alice.json
```

| Параметр | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `hero_name` | обязательный | Имя героя в `hero_def.json` и prompt анализа | `main_hero_definition.py` → `utils/hero_definition.py` | `run_hero_definition.bat` |
| `hero_image_dir` | обязательный | Каталог эталонных фотографий героя | `main_hero_definition.py` | `run_hero_definition.bat` |
| `human_detail_txt` | обязательный | Текстовый контекст о герое; поддерживает, но не заменяет визуальные признаки | `main_hero_definition.py` | `run_hero_definition.bat` |
| `reports_dir` | обязательный | Каталог отчётов; используется для default `hero_def.json` | `main_hero_definition.py` | `run_hero_definition.bat` |
| `output_path` | `reports_dir/hero_def.json` | Явный путь итогового JSON | `main_hero_definition.py` | `run_hero_definition.bat` |
| `model` | `gpt-4.1` или `OPENAI_HERO_DEFINITION_MODEL` | OpenAI vision-модель | `api/openai_hero_definition.py` | `run_hero_definition.bat` |
| `language` | `ru` | Язык значений в определении героя | `api/openai_hero_definition.py` | `run_hero_definition.bat` |
| `max_image_edge` | `1024`, минимум `256` | Максимальная сторона изображения перед отправкой | `api/openai_hero_definition.py` | `run_hero_definition.bat` |
| `image_extensions` | `.jpg`, `.jpeg`, `.png`, `.webp` | Допустимые форматы эталонов | `utils/hero_definition.py` | `run_hero_definition.bat` |

## 2. Sequence Trim Review: общие параметры

Пример запуска:

```bat
run_sequence_trim_review.bat sequence_trim_review_Alice_1.json
```

| Параметр | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `project_path` | обязательный | Исходный Premiere `.prproj` | `main_sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `source_sequence_name` | auto-select, если пусто | Исходная sequence | `main_sequence_trim_review.py` → `utils/premiere_project.py` | `run_sequence_trim_review.bat` |
| `new_sequence_name` | `<source>_trim_review` | Базовое имя review sequence и bundle | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `new_sequence_name_heuristic` | `<base>_heuristic` | Имя результата heuristic | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `new_sequence_name_semantic` | `<base>_semantic` | Имя результата semantic | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `new_sequence_name_hero` | `<base>_hero` | Имя hero-aware результата | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `output_project_path` | `<project>_trim_review.prproj` | Итоговый review-проект | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `reports_dir` | `<project_dir>/trim_review_reports` | JSON/TXT, кадры, кэш и progress log | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `engines` | `["heuristic","semantic"]` | Один или несколько движков: `heuristic`, `semantic`, `hero` | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `compact_keep` | `true` | Ограничивает KEEP короткими читаемыми островами | `utils/sequence_trim_classifier.py`, `utils/sequence_trim_semantic.py` | `run_sequence_trim_review.bat` |
| `photo_keep_min_seconds` | `1.5` | Минимальный KEEP для фото | `utils/sequence_trim_classifier.py` | `run_sequence_trim_review.bat` |
| `photo_keep_max_seconds` | `3.0` | Максимальный KEEP для фото | `utils/sequence_trim_classifier.py` | `run_sequence_trim_review.bat` |
| `video_keep_min_seconds` | `2.0` | Минимальный generic KEEP для видео | `utils/sequence_trim_classifier.py` | `run_sequence_trim_review.bat` |
| `video_keep_max_seconds` | `8.0` | Максимальный generic KEEP для видео | `utils/sequence_trim_classifier.py` | `run_sequence_trim_review.bat` |
| `target_keep_seconds` | код `300`, template `180` | Целевая общая длительность generic KEEP; hero engine не использует бюджет | `utils/sequence_trim_classifier.py`, `utils/sequence_trim_semantic.py` | `run_sequence_trim_review.bat` |
| `min_keep_seconds` | код `180`, template `120` | Нижняя граница generic KEEP | те же | `run_sequence_trim_review.bat` |
| `max_keep_seconds` | target, template `240` | Верхняя граница generic KEEP | те же | `run_sequence_trim_review.bat` |
| `context_notes` | пустая строка | Сюжетный контекст для semantic и отчёта | `api/openai_trim_semantic.py` | `run_sequence_trim_review.bat` |
| `force_keep_names` | `[]` | Подстроки имён клипов, которые нужно сохранить | `utils/sequence_trim_classifier.py` | `run_sequence_trim_review.bat` |
| `force_drop_names` | `[]` | Подстроки имён клипов, которые нужно удалить | `utils/sequence_trim_classifier.py` | `run_sequence_trim_review.bat` |
| `split_tracks` | `true` | Разнести generic KEEP/DROP по разным tracks | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |
| `keep_track_index` | `0` = V1 | Track для KEEP | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |
| `drop_track_index` | `1` = V2 | Track для DROP | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |
| `write_project` | `true` | Записывать итоговый `.prproj` | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |

### 2.1. Semantic engine

| Параметр | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `semantic_frames_per_clip` | код `5`, template `4` | Число кадров для анализа одного видео | `utils/sequence_trim_semantic.py` | `run_sequence_trim_review.bat` |
| `semantic_model` | `gpt-4.1-mini` | OpenAI vision-модель generic semantic анализа | `api/openai_trim_semantic.py` | `run_sequence_trim_review.bat` |
| `semantic_frames_dir` | `reports_dir/semantic_frames` | Каталог извлечённых кадров | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `semantic_request_timeout_seconds` | `180` | Тайм-аут одного OpenAI-запроса | `api/openai_trim_semantic.py` | `run_sequence_trim_review.bat` |

### 2.2. Hero engine

| Параметр | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `hero_definition_path` | обязательный для `hero` | Путь к ранее проверенному `hero_def.json` | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_match_model` | `gpt-4.1` или `OPENAI_HERO_MATCH_MODEL` | Модель сравнения героя с кадрами | `api/openai_hero_match.py` | `run_sequence_trim_review.bat` |
| `hero_frame_interval_seconds` | `5.0` | Интервал семплирования видео | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_max_frames_per_clip` | `48` | Верхний предел кадров одного клипа | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_frames_per_request` | `10` | Кадров клипа в одном OpenAI-запросе | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_reference_image_limit` | `6` | Число эталонных фотографий в запросе | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_pre_roll_seconds` | `10.0` | Контекст до обнаружения героя | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_post_roll_seconds` | `10.0` | Контекст после обнаружения героя | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_keep_medium_matches` | `true` | Оставлять MEDIUM для ручной проверки | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_keep_clip_on_analysis_error` | `true` | При ошибке API не удалять клип, а отправлять в REVIEW | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_high_confidence_threshold` | `0.85` | Минимальная confidence для HIGH | `api/openai_hero_match.py` | `run_sequence_trim_review.bat` |
| `hero_medium_confidence_threshold` | код `0.55`, Alice `0.60` | Минимальная confidence для MEDIUM | `api/openai_hero_match.py` | `run_sequence_trim_review.bat` |
| `hero_max_image_edge` | `1024` | Размер эталонов и кадров перед API | `api/openai_hero_match.py` | `run_sequence_trim_review.bat` |
| `hero_request_timeout_seconds` | `180` | Тайм-аут одного hero OpenAI-запроса | `api/openai_hero_match.py` | `run_sequence_trim_review.bat` |
| `hero_frames_dir` | `reports_dir/hero_frames` | Каталог кадров для hero-анализа | `utils/sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `hero_cache_dir` | `reports_dir/hero_match_cache` | Поклиповый кэш решений | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |
| `hero_resume_from_cache` | `true` | Продолжать повторный запуск без оплаты уже готовых клипов | `utils/sequence_trim_hero.py` | `run_sequence_trim_review.bat` |

## 3. Report Replay без OpenAI

Пример запуска:

```bat
run_sequence_trim_review.bat sequence_trim_review_Alice_replay_levels.json
```

| Параметр | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `mode` | обязательное значение `report_replay` | Переключает CLI с анализа на чтение готового отчёта | `main_sequence_trim_review.py` | `run_sequence_trim_review.bat` |
| `review_json_path` | обязательный | Per-engine JSON с готовыми segment decisions; TXT не используется как машинный источник | `utils/sequence_trim_report_replay.py` | `run_sequence_trim_review.bat` |
| `project_path` | из отчёта | Исходный Premiere-проект | `utils/sequence_trim_report_replay.py` | `run_sequence_trim_review.bat` |
| `output_project_path` | `<source>_hero_levels.prproj` | Проект с общей level-sequence | `utils/sequence_trim_report_replay.py` | `run_sequence_trim_review.bat` |
| `reports_dir` | `<report_dir>/hero_level_replay` | Replay summary и progress log | `utils/sequence_trim_report_replay.py` | `run_sequence_trim_review.bat` |
| `sequence_name` | `<review_name>_LEVEL_TRACKS` | Имя одной итоговой sequence | `utils/sequence_trim_report_replay.py` | `run_sequence_trim_review.bat` |
| `track_indexes.high` | `0` = V1 | Track уровня HIGH | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |
| `track_indexes.medium` | `1` = V2 | Track уровня MEDIUM | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |
| `track_indexes.review` | `2` = V3 | Track уровня REVIEW | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |
| `track_indexes.drop` | `3` = V4 | Track уровня DROP | `utils/premiere_trim_review_export.py` | `run_sequence_trim_review.bat` |

## 4. Project Sequence Batch

Пример запуска:

```bat
run_project_sequence_batch.bat project_sequence_batch_Alice.json
```

| Параметр | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `project_path` | обязательный | Исходный Premiere-проект | `main_project_sequence_batch.py` | `run_project_sequence_batch.bat`, проектные wrappers |
| `regeneration_assets_dir` | обязательный | Метаданные/ассеты кандидатов sequence | `main_project_sequence_batch.py` | те же |
| `output_project_path` | обязательный | Имя итогового оптимизированного `.prproj` | `main_project_sequence_batch.py` | те же |
| `reports_dir` | вычисляется из assets | Финальный каталог отчётов; старый универсальный ключ | `utils/project_sequence_batch.py` | те же |
| `staging_reports_dir` | auto hidden sibling | Временная staging-папка | `utils/project_sequence_batch.py` | те же |
| `final_reports_dir` | из `regeneration_assets_dir` | Явный финальный каталог отчётов | `utils/project_sequence_batch.py` | те же |
| `engine` | `heuristic` | Движок оптимизации порядка | `main_sequence_optimizer.py` через batch | те же |
| `translation_results_path` | `null` | FCP Translation Results для диагностики/контекста | `utils/project_sequence_batch.py` | те же |
| `prin_path` | legacy alias | Старое имя `translation_results_path` | `utils/project_sequence_batch.py` | те же |
| `transition_mode` | `disabled` | `disabled`, `recommend_only`, `apply` | `utils/project_sequence_batch.py` | те же |
| `enable_auto_transitions` | `false` | Legacy-включение переходов; нормализуется через `transition_mode` | `utils/project_sequence_batch.py` | те же |
| `enable_visual_transitions` | `false` | Разрешает переходы также для mixed photo/video | `main_sequence_optimizer.py` | те же |
| `enable_auto_durations` | `false` | Автоматически меняет длительности visual clips | `main_sequence_optimizer.py` | те же |
| `enable_auto_transforms` | `false` | Планирует Transform для still images | `main_sequence_optimizer.py` | те же |
| `include_visual_media` | `false` | Включает фото вместе с видео | `main_sequence_optimizer.py` | те же |
| `enable_subject_series_grouping` | `false` | Группирует серии одного сюжета/объекта | `main_sequence_optimizer.py` | те же |
| `allow_transition_handle_trimming` | `false` | Разрешает подрезать handles ради перехода | `main_sequence_optimizer.py` | те же |
| `transition_template_project_path` | `null` | `.prproj`-источник шаблонов переходов | `main_sequence_optimizer.py` | те же |
| `generate_premiere_transition_script` | `transition_mode=="apply"` | Создаёт JSX применения переходов | `utils/project_sequence_batch.py` | те же |
| `premiere_transition_script_name` | `Cross Dissolve` | Имя эффекта перехода для JSX | `utils/premiere_transition_script.py` | те же |
| `premiere_transition_script_duration_seconds` | `1.0` | Длительность перехода | `utils/premiere_transition_script.py` | те же |
| `premiere_transition_script_track_index` | `0` | Video track для JSX переходов | `utils/premiere_transition_script.py` | те же |
| `premiere_transition_script_save_project` | `true` | Сохранять проект после JSX | `utils/premiere_transition_script.py` | те же |
| `generate_premiere_transform_script` | значение `enable_auto_transforms` | Создаёт JSX Transform | `utils/premiere_transform_script.py` | те же |
| `premiere_transform_script_track_index` | `0` | Video track для Transform JSX | `utils/premiere_transform_script.py` | те же |
| `premiere_transform_script_save_project` | `true` | Сохранять проект после Transform JSX | `utils/premiere_transform_script.py` | те же |
| `premiere_transform_script_default_effect_name` | `Transform` | Fallback-эффект | `utils/premiere_transform_script.py` | те же |
| `premiere_transform_script_add_video_effects` | код `false`, template `true` | Добавлять именованные Premiere Transform effects | `utils/premiere_transform_script.py` | те же |
| `premiere_transform_script_apply_safe_effect` | код `true`, template `false` | Применять безопасный fallback | `utils/premiere_transform_script.py` | те же |
| `generate_personalized_report` | `false` | Создать персонализированный отчёт героя и музыки | `utils/project_sequence_batch.py` | те же |
| `human_detail_txt` | обязателен при personalized | Текстовый профиль героя для human/music overlay | `utils/human_profile_sequence_report.py` | те же |
| `sequence_jobs` | обязательный непустой список | Набор sequence для последовательной обработки | `utils/project_sequence_batch.py` | те же |
| `sequence_jobs[].source_sequence_name` | обязательный | Исходная sequence конкретного job | `utils/project_sequence_batch.py` | те же |
| `sequence_jobs[].new_sequence_name` | `<source>__optimized` | Имя оптимизированной sequence | `utils/project_sequence_batch.py` | те же |

## 5. Прямые CLI-программы отчётов

Эти программы пока не имеют `.bat`; в колонке Batch это указано явно.

### `main_human_sequence_report.py`

| Параметр CLI | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `--optimization-report-json` | обязательный | Готовый optimization/manual-order JSON | `main_human_sequence_report.py` | нет |
| `--human-detail-txt` | обязательный | Текстовый профиль героя | `main_human_sequence_report.py` | нет |
| `--output-report-txt` | рядом с JSON | Путь персонализированного отчёта | `main_human_sequence_report.py` | нет |

### `main_sequence_reports.py`

| Параметр CLI | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `--prproj` | обязательный | Проект после ручного монтажа | `main_sequence_reports.py` | нет |
| `--sequence-name` | обязательный | Текущая sequence | `main_sequence_reports.py` | нет |
| `--optimization-report-json` | обязательный | Исходные candidate metadata | `main_sequence_reports.py` | нет |
| `--output-dir` | каталог optimization JSON | Общая выходная папка | `main_sequence_reports.py` | нет |
| `--output-json` | auto | Current-order JSON | `main_sequence_reports.py` | нет |
| `--output-music-txt` | auto | Music-first TXT | `main_sequence_reports.py` | нет |
| `--output-structure-txt` | auto | Structure TXT | `main_sequence_reports.py` | нет |
| `--output-transition-txt` | auto | Transition TXT | `main_sequence_reports.py` | нет |
| `--music-only` | `false` | Не создавать structure/transition | `main_sequence_reports.py` | нет |

### `main_sequence_music_first.py`

| Параметр CLI | Обязательность / default | Назначение | Программа | Batch |
|---|---|---|---|---|
| `--prproj` | обязательный | Premiere-проект | `main_sequence_music_first.py` | нет |
| `--sequence-name` | обязательный | Анализируемая sequence | `main_sequence_music_first.py` | нет |
| `--output-dir` | `Settings.output_dir` | Выходная папка | `main_sequence_music_first.py` | нет |
| `--output-json` | auto | Scene/profile JSON | `main_sequence_music_first.py` | нет |
| `--output-music-txt` | auto | Music-first TXT | `main_sequence_music_first.py` | нет |
| `--output-structure-txt` | auto | Structure TXT | `main_sequence_music_first.py` | нет |
| `--output-transition-txt` | auto | Transition TXT | `main_sequence_music_first.py` | нет |
| `--max-sampled-clips` | `12` | Число representative clips | `main_sequence_music_first.py` | нет |
| `--max-analyzed-clips` | без ограничения | Верхний предел полного анализа | `main_sequence_music_first.py` | нет |
| `--full-recommendations` | `false` | Добавить structure и transitions | `main_sequence_music_first.py` | нет |
| `--scene-model` | default scene model | OpenAI model override | `main_sequence_music_first.py` | нет |

## Правила сопровождения

При добавлении или переименовании параметра необходимо одновременно обновить:

1. Python consumer;
2. template JSON;
3. строку в этой матрице;
4. пример запуска в пользовательском руководстве;
5. тест, подтверждающий default и передачу параметра;
6. project-specific config только при необходимости.

Batch-файл не должен скрыто менять смысл JSON-параметра. Если `.bat` задаёт default или добавляет CLI-флаг, это должно быть указано в матрице.
