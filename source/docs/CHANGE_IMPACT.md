# Change Impact Guide

This document is generated from `project_structure_registry.json` and helps operators understand what must be reviewed when the source project changes.

## Core Invariants

- GenerationConfig в config.py является единой точкой правды для generation-флагов.
- Все stage-артефакты одного этапа должны иметь общий префикс stage_id.
- Изменение naming contract требует проверки всех consumers, которые извлекают stage_id или строят пути по имени файла.
- Одновременно можно включить только один framing-режим: prefer_face_closeups, use_ai_optimal_framing, generate_dual_framing_videos.
- main_grok_batch.py по умолчанию очищает input/ и output/ после успешного batch-run, если не указан keep-workdirs.
- main_full_pipeline.py может удалять обработанные входные изображения из input/.
- mp4 доставляются в final_videos_dir, а non-video stage assets - в regeneration_assets_dir/<stage_id>/.

## Change Types

### `generation_flag`

Добавление нового generation-флага или изменение semantics существующего.

Must update:
- `config.py`
- `config.json`
- `config_BASE.json`
- `main.py`
- `main_desktop_pipeline.py`
- `api/openai_prompt_synthesizer.py`
- `utils/prompt_builder.py`
- `USER_GUIDE.md`
- `Руководство_пользователя.md`

Must review:
- `main_full_pipeline.py`
- `config_*.json`
- `tests/test_selfie_phone_prompt.py`
- `test/test_motion_selection.py`
- `test/test_scene_pipeline_integration.py`

Recommended tests:
- `test/test_config_cli_defaults.py`
- `test/test_scene_pipeline_integration.py`
- `test/test_motion_selection.py`
- `test/test_video_framing_modes.py`
- `tests/test_selfie_phone_prompt.py`

Minimum checks:
- config load
- CLI override
- prompt output
- manifest serialization
- targeted tests

### `scene_schema`

Изменение схемы scene-analysis payload.

Must update:
- `models/scene_analysis.py`
- `api/openai_scene.py`

Must review:
- `main_scene.py`
- `main.py`
- `api/openai_prompt_synthesizer.py`
- `utils/prompt_builder.py`
- `utils/project_sequence_batch.py`
- `utils/current_sequence_reports.py`
- `utils/human_profile_sequence_report.py`

Recommended tests:
- `test/test_openai_scene.py`
- `test/test_scene_app.py`
- `test/test_scene_pipeline_integration.py`

Minimum checks:
- scene json serialization
- scene parsing tests
- prompt integration tests

### `artifact_naming`

Изменение naming rules, stage_id или имен файлов output-артефактов.

Must update:
- `main.py`
- `main_desktop_pipeline.py`
- `main_grok_web.py`
- `main_grok_batch.py`

Must review:
- `main_full_pipeline.py`
- `utils/project_delivery.py`
- `utils/premiere_xml.py`
- `utils/premiere_project.py`
- `utils/fcp_translation_results.py`
- `utils/current_sequence_reports.py`

Recommended tests:
- `test/test_api_pipeline.py`
- `test/test_grok_web_app.py`
- `test/test_grok_batch_app.py`
- `test/test_sequence_optimizer_app.py`

Minimum checks:
- single-stage prompt generation
- grok single-stage
- grok batch
- sequence optimizer compatibility

### `grok_runtime`

Изменение браузерной автоматизации Grok, таймаутов или flow подготовки background/video.

Must update:
- `api/grok_web.py`
- `main_grok_web.py`

Must review:
- `main_grok_batch.py`
- `main_full_pipeline.py`
- `utils/project_delivery.py`

Recommended tests:
- `test/test_grok_web_app.py`
- `test/test_grok_batch_app.py`
- `test/test_full_pipeline.py`
- `test/test_project_delivery.py`

Minimum checks:
- single-stage run
- batch run
- background-only run
- no-submit mode

### `delivery_cleanup`

Изменение правил синхронизации, очистки, архивации или error-handling.

Must update:
- `utils/project_delivery.py`

Must review:
- `main_grok_batch.py`
- `main_full_pipeline.py`
- `utils/artifact_cleanup.py`
- `main_cleanup_artifacts.py`

Recommended tests:
- `test/test_project_delivery.py`
- `test/test_full_pipeline.py`
- `test/test_artifact_cleanup.py`

Minimum checks:
- final media copy
- regeneration assets sync
- error directory flow
- no unexpected input deletion

### `sequence_optimizer`

Изменение логики sequence optimization, export или reporting.

Must update:
- `main_sequence_optimizer.py`
- `utils/sequence_optimizer.py`
- `utils/sequence_optimizer_runtime.py`
- `models/video_sequence.py`

Must review:
- `utils/premiere_xml.py`
- `utils/premiere_project.py`
- `utils/premiere_xml_export.py`
- `utils/premiere_project_export.py`
- `utils/sequence_structure_report.py`
- `utils/transition_recommendations.py`
- `utils/project_sequence_batch.py`

Recommended tests:
- `test/test_sequence_optimizer_app.py`

Minimum checks:
- optimizer tests
- json/txt reports
- xml/prproj export if affected

## Command Examples

```powershell
python .\main_change_impact.py --change-type generation_flag --changed-file config.py
python .\main_change_impact.py --changed-file main_grok_web.py --json
```
