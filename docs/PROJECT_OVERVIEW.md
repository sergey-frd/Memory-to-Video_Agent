# Project Overview

This document is generated from the source project and is intended for the external project-information repository.

- Generated at: `2026-08-25T23:54:53+02:00`
- Source project: `img-style-ag_1`

## Snapshot

- Files scanned: `568`
- Python files: `240`
- Test files: `44`
- Config JSON files: `40`
- Markdown docs: `28`
- Entry points: `31`
- API modules: `17`
- Utils modules: `46`
- Model modules: `6`

## Entry Points

- `main.py`
- `main1.py`
- `main_change_impact.py`
- `main_chatgpt_portrait_batch.py`
- `main_cleanup_artifacts.py`
- `main_copy_sequence_images.py`
- `main_copy_sequence_media_batch.py`
- `main_desktop.py`
- `main_desktop_pipeline.py`
- `main_full_pipeline.py`
- `main_full_pipeline_api.py`
- `main_grok_batch.py`
- `main_grok_pipeline.py`
- `main_grok_profile_check.py`
- `main_grok_web.py`
- `main_hero_definition.py`
- `main_human_sequence_report.py`
- `main_premiere_import_keep.py`
- `main_premiere_transform_script.py`
- `main_premiere_transition_script.py`
- `main_project_publication.py`
- `main_project_publication_push.py`
- `main_project_sequence_batch.py`
- `main_scene.py`
- `main_sequence_music_first.py`
- `main_sequence_optimizer.py`
- `main_sequence_presentation.py`
- `main_sequence_reports.py`
- `main_sequence_trim_review.py`
- `main_video_prompt_composer.py`
- `main_video_prompt_story.py`

## Subsystems

| Id | Purpose |
| --- | --- |
| `config` | Флаги генерации, валидация, canonical paths. |
| `scene_analysis` | Схема scene payload и ее получение. |
| `prompt_generation` | Сборка video/background/final-frame/music prompts и motion selection. |
| `grok_runtime` | Запуск Grok для single-stage и batch сценариев. |
| `delivery_lifecycle` | Синхронизация, очистка, перенос ошибок, доставка итогов. |
| `sequence_optimization` | Парсинг sequence, оптимизация порядка, экспорт XML/PRPROJ и отчеты. |
| `hero_definition` | Создание проверяемого визуального определения героя из эталонных изображений и human-detail текста. |
| `sequence_trim_review` | KEEP/DROP-анализ Premiere sequence через heuristic, semantic, hero, report_replay, ручное применение KEEP JSON, импорт списка медиа и import-and-keep за один проход. |

## Change Types

| Id | Description |
| --- | --- |
| `generation_flag` | Добавление нового generation-флага или изменение semantics существующего. |
| `scene_schema` | Изменение схемы scene-analysis payload. |
| `artifact_naming` | Изменение naming rules, stage_id или имен файлов output-артефактов. |
| `grok_runtime` | Изменение браузерной автоматизации Grok, таймаутов или flow подготовки background/video. |
| `delivery_cleanup` | Изменение правил синхронизации, очистки, архивации или error-handling. |
| `sequence_optimizer` | Изменение логики sequence optimization, export или reporting. |
