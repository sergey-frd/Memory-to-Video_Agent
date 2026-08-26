# Premiere JSON: модификация sequence и intrinsic Motion

Проект поддерживает два программно исполняемых JSON-режима:

1. `premiere_sequence_motion_animation` — дублирует готовую sequence и добавляет
   intrinsic `Motion > Scale/Position`.
2. `premiere_sequence_insert_from_sequence_and_motion_animation` — дополнительно
   вставляет video-only диапазон из другой sequence того же `.prproj`, затем
   анимирует только допустимые статичные изображения.

Оба режима запускаются одним entry point:

```bat
.\run_premiere_sequence_motion.bat .\config.json --dry-run
.\run_premiere_sequence_motion.bat .\config.json
```

Прямой Python-запуск:

```powershell
python .\main_premiere_import_keep.py --config .\config.json --dry-run
python .\main_premiere_import_keep.py --config .\config.json
```

Всегда сначала запускайте `--dry-run`. Полный запуск блокируется при
существующей output sequence, существующем Save As-файле, offline media или
неоднозначном frame-exact диапазоне.

## Пример 1: только Motion

Основа — `premiere_sequence_motion_template.json`.

```json
{
  "schema_version": "1.0",
  "mode": "premiere_sequence_motion_animation",
  "project": {
    "project_file": "<LOCAL_PATH>",
    "save_as_project_file": "<LOCAL_PATH>"
  },
  "sequences": {
    "source_sequence_name": "approved_v05",
    "output_sequence_name": "approved_v10"
  },
  "target_selection": {
    "include_ranges_seconds": [[0.0, 60.0]],
    "protected_ranges_seconds": [[13.0, 19.32]],
    "minimum_visible_duration_frames": 21
  },
  "motion_animation": {
    "temporal_interpolation": "LINEAR_OR_NEAR_LINEAR_WITH_NO_STATIONARY_HEAD_OR_TAIL",
    "motion_profiles": [
      {
        "name": "SHORT_VISIBLE",
        "visible_duration_frames_min": 21,
        "visible_duration_frames_max": 37,
        "scale_delta_percent_of_baseline": 3.0,
        "max_position_delta_percent_of_frame": 0.8
      }
    ],
    "direction_cycle": ["PUSH_IN", "PUSH_OUT"]
  },
  "audio_policy": {"mode": "OUTPUT_SILENT"},
  "dry_run": {
    "required": true,
    "required_plan_filename": "motion_dry_run.json"
  },
  "review_export": {
    "filename": "approved_v10_640_360.mp4",
    "actual_frame_size": {"width": 640, "height": 360},
    "frame_rate_fps": 25,
    "expected_frames": 1500
  }
}
```

Правила:

- Scale/Position рассчитываются относительно существующего baseline, а не от
  абсолютных `100%` и `0.5:0.5`;
- protected range исключает целиком любой пересекающий его клип;
- два keyframe ставятся на первый и последний видимый кадр;
- `IsTimeVarying=true`;
- исходный `.prproj` и source sequence не изменяются;
- аудио удаляется только из output sequence non-ripple способом.

## Пример 2: вставка из sequence + Motion

Основа — `premiere_sequence_insert_motion_template.json`.

```json
{
  "schema_version": "1.0",
  "mode": "premiere_sequence_insert_from_sequence_and_motion_animation",
  "project": {
    "project_file": "<LOCAL_PATH>",
    "save_as_project_file": "<LOCAL_PATH>"
  },
  "sequences": {
    "main_source_sequence_name": "film_v09",
    "correction_source_sequence_name": "correction_sequence",
    "output_sequence_name": "film_v10",
    "correction_source_is_sequence_not_media_file": true
  },
  "semantic_source_range_resolution": {
    "sequence": "correction_sequence",
    "candidate_ranges_frames": [[160, 248], [166, 254], [175, 263]],
    "resolved_source_range_frames": [166, 254],
    "preferred_visible_duration_seconds": {
      "min": 2.5,
      "target": 3.5,
      "max": 4.5
    }
  },
  "destination_insertion": {
    "policy": "INSERT_IMMEDIATELY_BEFORE_FINAL_STYLIZED_CODA",
    "resolved_destination_frame": 1849
  }
}
```

Ключевые поля:

- `correction_source_sequence_name` — имя sequence внутри проекта. Это не имя
  внешнего MP4;
- `resolved_source_range_frames` — `[IN, OUT_EXCLUSIVE]` в timeline коррекции;
- `candidate_ranges_frames` — варианты, которые попадут в dry-run;
- `resolved_destination_frame` — frame-exact граница существующего picture item;
- если `resolved_destination_frame` не задан, исполнитель выбирает начало
  последней непрерывной группы изображений;
- вставка берёт только video, сохраняет source IN/OUT и скорость;
- последующие picture items сдвигаются ровно на длительность вставки;
- вставленный live-фрагмент и остальные natural-motion видео не получают
  дополнительный Motion.

Остальные обязательные блоки (`sequence_versioning`, `sequence_contract`,
`motion_animation`, `audio_policy`, `dry_run`, `review_export`, `deliverables`)
полностью показаны в шаблоне.

## Проверка результата

Dry-run должен содержать:

- точные имена project/source/correction/output;
- выбранные source IN/OUT и destination frame;
- будущую длительность output;
- списки animated static, natural-motion, protected и blocked items;
- Scale/Position endpoints и профили;
- planned `IsTimeVarying`;
- число non-ripple audio removals.

QA полного запуска проверяет:

- source project и все source sequences не изменились;
- output sequence существует ровно один раз;
- ripple shift равен длительности вставки;
- вставленный range сохранил IN/OUT и не получил Motion;
- статичные кадры имеют два Scale и два Position keyframe;
- output и review не имеют аудио;
- review совпадает с output по fps и числу кадров.

## Файлы реализации

- `main_premiere_import_keep.py`
- `utils/premiere_sequence_motion.py`
- `utils/premiere_sequence_insert_motion.py`
- `models/premiere_sequence_motion.py`
- `run_premiere_sequence_motion.bat`
- `premiere_sequence_motion_template.json`
- `premiere_sequence_insert_motion_template.json`
- `test/test_premiere_sequence_motion.py`
- `test/test_premiere_sequence_insert_motion.py`
