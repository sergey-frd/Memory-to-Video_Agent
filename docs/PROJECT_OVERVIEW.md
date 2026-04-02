# Project Overview

This document is generated from the source project and is intended for the external project-information repository.

- Generated at: `2026-04-02T02:05:41+02:00`
- Source project: `img-style-ag_1`

## Snapshot

- Files scanned: `129`
- Python files: `82`
- Test files: `25`
- Config JSON files: `12`
- Markdown docs: `3`
- Entry points: `19`
- API modules: `10`
- Utils modules: `23`
- Model modules: `3`

## Entry Points

- `main.py`
- `main1.py`
- `main_change_impact.py`
- `main_cleanup_artifacts.py`
- `main_desktop.py`
- `main_desktop_pipeline.py`
- `main_full_pipeline.py`
- `main_grok_batch.py`
- `main_grok_pipeline.py`
- `main_grok_profile_check.py`
- `main_grok_web.py`
- `main_human_sequence_report.py`
- `main_project_publication.py`
- `main_project_publication_push.py`
- `main_project_sequence_batch.py`
- `main_scene.py`
- `main_sequence_music_first.py`
- `main_sequence_optimizer.py`
- `main_sequence_reports.py`

## Subsystems

| Id | Purpose |
| --- | --- |
| `config` | Флаги генерации, валидация, canonical paths. |
| `scene_analysis` | Схема scene payload и ее получение. |
| `prompt_generation` | Сборка video/background/final-frame/music prompts и motion selection. |
| `grok_runtime` | Запуск Grok для single-stage и batch сценариев. |
| `delivery_lifecycle` | Синхронизация, очистка, перенос ошибок, доставка итогов. |
| `sequence_optimization` | Парсинг sequence, оптимизация порядка, экспорт XML/PRPROJ и отчеты. |

## Change Types

| Id | Description |
| --- | --- |
| `generation_flag` | Добавление нового generation-флага или изменение semantics существующего. |
| `scene_schema` | Изменение схемы scene-analysis payload. |
| `artifact_naming` | Изменение naming rules, stage_id или имен файлов output-артефактов. |
| `grok_runtime` | Изменение браузерной автоматизации Grok, таймаутов или flow подготовки background/video. |
| `delivery_cleanup` | Изменение правил синхронизации, очистки, архивации или error-handling. |
| `sequence_optimizer` | Изменение логики sequence optimization, export или reporting. |
