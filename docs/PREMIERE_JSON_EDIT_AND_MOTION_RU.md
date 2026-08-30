# Premiere JSON: модификация sequence и intrinsic Motion

Переносимый entry point поддерживает два программно исполняемых Motion JSON-режима.
Специализированные TASK_019–030/Alla описаны отдельно в
[PREMIERE_TASK_WORKFLOWS_RU.md](PREMIERE_TASK_WORKFLOWS_RU.md); у них другой CLI
и правила записи проекта.

Два переносимых режима:

1. `premiere_sequence_motion_animation` — дублирует готовую sequence и добавляет
   intrinsic `Motion > Scale/Position`.
2. `premiere_sequence_insert_from_sequence_and_motion_animation` — дополнительно
   вставляет video-only диапазон из другой sequence того же `.prproj`, затем
   анимирует только допустимые статичные изображения.

Оба режима запускаются одним entry point:

```bat
.\run_premiere_sequence_motion.bat .\premiere_sequence_motion_template.json --dry-run
.\run_premiere_sequence_motion.bat .\premiere_sequence_motion_template.json
```

Прямой Python-запуск:

```powershell
python .\main_premiere_import_keep.py --config .\premiere_sequence_motion_template.json --dry-run
python .\main_premiere_import_keep.py --config .\premiere_sequence_motion_template.json
```

Сначала заполните шаблон и запускайте `--dry-run`: проверка требует реального
проекта и доступных media, записывает отчёты, но не изменяет `.prproj`. Полный запуск блокируется при
существующей output sequence, существующем Save As-файле, offline media или
неоднозначном frame-exact диапазоне.

## Пример 1: только Motion

Основа — `premiere_sequence_motion_template.json`.

```json
{
  "schema_version": "1.0",
  "task_id": "PREMIERE_SEQUENCE_MOTION_TEMPLATE",
  "mode": "premiere_sequence_motion_animation",
  "project": {
    "project_file": "<LOCAL_PATH>",
    "save_as_project_file": "<LOCAL_PATH>",
    "never_overwrite_source_project": true,
    "save_as_before_editing": true
  },
  "sequences": {
    "source_sequence_name": "approved_source_sequence",
    "output_sequence_name": "animated_output_sequence",
    "source_sequence_read_only": true,
    "abort_if_output_sequence_already_exists": true
  },
  "sequence_contract": {
    "edit_timebase_fps": 25,
    "expected_frame_size": {
      "width": 3840,
      "height": 2160
    },
    "expected_duration_seconds": 60.0,
    "expected_frames": 1500
  },
  "target_selection": {
    "include_ranges_seconds": [
      [
        0.0,
        60.0
      ]
    ],
    "protected_ranges_seconds": [],
    "minimum_visible_duration_frames": 21,
    "minimum_visible_duration_seconds": 0.84
  },
  "motion_animation": {
    "apply_values_relative_to_existing_baseline": true,
    "motion_profiles": [
      {
        "name": "SHORT_SUBTLE",
        "visible_duration_frames_min": 21,
        "visible_duration_frames_max": 37,
        "scale_delta_percent_of_baseline": 1.5,
        "max_position_delta_percent_of_frame": 0.35
      },
      {
        "name": "MEDIUM_GENTLE",
        "visible_duration_frames_min": 38,
        "visible_duration_frames_max": 75,
        "scale_delta_percent_of_baseline": 3.0,
        "max_position_delta_percent_of_frame": 0.7
      },
      {
        "name": "LONG_BREATHING",
        "visible_duration_frames_min": 76,
        "visible_duration_frames_max": null,
        "scale_delta_percent_of_baseline": 5.0,
        "max_position_delta_percent_of_frame": 1.0
      }
    ],
    "direction_cycle": [
      "PUSH_IN",
      "PUSH_OUT",
      "GENTLE_PAN_LEFT_TO_RIGHT_WITH_SAFE_OVERSCAN",
      "GENTLE_PAN_RIGHT_TO_LEFT_WITH_SAFE_OVERSCAN"
    ],
    "temporal_interpolation": "LINEAR_OR_NEAR_LINEAR_WITH_NO_STATIONARY_HEAD_OR_TAIL"
  },
  "audio_policy": {
    "mode": "OUTPUT_SILENT",
    "remove_all_audio_clips_from_output_sequence": true,
    "leave_empty_audio_tracks_available_for_future_soundtrack": true
  },
  "dry_run": {
    "required": true,
    "required_plan_filename": "premiere_sequence_motion_dry_run.json",
    "expected_output_frames": 1500,
    "expected_output_duration_seconds": 60.0
  },
  "review_export": {
    "required": true,
    "source_sequence": "animated_output_sequence",
    "filename": "animated_output_sequence_640_360.mp4",
    "actual_frame_size": {
      "width": 640,
      "height": 360
    },
    "frame_rate_fps": 25,
    "expected_frames": 1500,
    "expected_duration_seconds": 60.0,
    "audio_stream_required": false
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
  "task_id": "PREMIERE_SEQUENCE_INSERT_AND_MOTION_TEMPLATE",
  "mode": "premiere_sequence_insert_from_sequence_and_motion_animation",
  "project": {
    "project_filename_exact": "source.prproj",
    "project_file": "<LOCAL_PATH>",
    "save_as_project_file": "<LOCAL_PATH>",
    "never_overwrite_source_project": true,
    "save_as_before_editing": true
  },
  "sequences": {
    "main_source_sequence_name": "approved_main_sequence_v09",
    "correction_source_sequence_name": "correction_sequence",
    "output_sequence_name": "approved_main_sequence_v10",
    "correction_source_is_sequence_not_media_file": true,
    "source_sequences_read_only": true,
    "abort_if_output_sequence_already_exists": true
  },
  "sequence_versioning": {
    "automated_milestone_increment": 5,
    "current_output_milestone": 10,
    "future_output_milestones": [
      15,
      20,
      25
    ],
    "never_overwrite_existing_sequence": true
  },
  "sequence_contract": {
    "expected_edit_timebase_fps": 25,
    "preserve_main_source_sequence_settings": true,
    "preserve_existing_main_clip_order_except_shift_after_insertion": true,
    "preserve_existing_non_motion_effects": true,
    "no_other_insertions_deletions_trims_or_retimes": true
  },
  "semantic_source_range_resolution": {
    "sequence": "correction_sequence",
    "video_only": true,
    "requested_moment": "Describe the selected live action here.",
    "approximate_search_window_seconds": [
      6.0,
      11.5
    ],
    "candidate_ranges_frames": [
      [
        160,
        248
      ],
      [
        166,
        254
      ],
      [
        175,
        263
      ]
    ],
    "resolved_source_range_frames": [
      166,
      254
    ],
    "preferred_visible_duration_seconds": {
      "min": 2.5,
      "target": 3.5,
      "max": 4.5
    }
  },
  "destination_insertion": {
    "main_sequence": "approved_main_sequence_v09",
    "policy": "INSERT_IMMEDIATELY_BEFORE_FINAL_STYLIZED_CODA",
    "resolved_destination_frame": 1849
  },
  "insert_operation": {
    "source_type": "SEQUENCE_RANGE",
    "source_sequence": "correction_sequence",
    "destination_sequence": "approved_main_sequence_v10",
    "include_video": true,
    "include_audio": false,
    "preserve_source_range_in_out": true,
    "preserve_source_speed": true,
    "do_not_apply_motion_animation_to_inserted_live_range": true
  },
  "motion_animation": {
    "effect": "Premiere Pro intrinsic Motion",
    "minimum_visible_duration_frames": 21,
    "temporal_interpolation": "LINEAR_OR_NEAR_LINEAR_WITH_NO_STATIONARY_HEAD_OR_TAIL",
    "motion_profiles": [
      {
        "name": "SHORT_VISIBLE",
        "visible_duration_frames_min": 21,
        "visible_duration_frames_max": 37,
        "scale_delta_percent_of_baseline": 3.0,
        "max_position_delta_percent_of_frame": 0.8
      },
      {
        "name": "MEDIUM_LIVELY",
        "visible_duration_frames_min": 38,
        "visible_duration_frames_max": 75,
        "scale_delta_percent_of_baseline": 5.5,
        "max_position_delta_percent_of_frame": 1.5
      },
      {
        "name": "LONG_EXPRESSIVE",
        "visible_duration_frames_min": 76,
        "visible_duration_frames_max": null,
        "scale_delta_percent_of_baseline": 8.0,
        "max_position_delta_percent_of_frame": 2.5
      }
    ],
    "direction_cycle": [
      "PUSH_IN",
      "PUSH_OUT",
      "PAN_LEFT_TO_RIGHT_WITH_SAFE_OVERSCAN",
      "PAN_RIGHT_TO_LEFT_WITH_SAFE_OVERSCAN"
    ]
  },
  "audio_policy": {
    "mode": "OUTPUT_SILENT",
    "remove_all_audio_clips_from_output_sequence": true,
    "audio_removal_must_be_non_ripple": true,
    "leave_empty_audio_tracks_available_for_future_manual_soundtrack": true
  },
  "dry_run": {
    "required": true,
    "required_plan_filename": "premiere_sequence_insert_motion_dry_run.json",
    "must_have_zero_blocked_items_before_execution": true
  },
  "review_export": {
    "required": true,
    "source_sequence": "approved_main_sequence_v10",
    "filename": "approved_main_sequence_v10_640_360.mp4",
    "actual_frame_size": {
      "width": 640,
      "height": 360
    },
    "frame_rate_fps": 25,
    "audio_stream_required": false
  },
  "deliverables": {
    "required_implementation_report": "premiere_sequence_insert_motion_implementation.txt",
    "required_dry_run_plan": "premiere_sequence_insert_motion_dry_run.json",
    "required_saved_project": "source_insert_motion.prproj",
    "required_output_sequence": "approved_main_sequence_v10",
    "required_review_export": "approved_main_sequence_v10_640_360.mp4",
    "required_qa_report": "premiere_sequence_insert_motion_qa.txt"
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

Оба примера выше — полные JSON-шаблоны. Перед запуском замените пути и имена,
проверьте fps/размер/длительность и согласуйте все поля output. Для второго
примера передавайте `premiere_sequence_insert_motion_template.json`.

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

## Интерполяция и ограничения preview

В обоих актуальных шаблонах явно задано
`motion_animation.temporal_interpolation = LINEAR_OR_NEAR_LINEAR_WITH_NO_STATIONARY_HEAD_OR_TAIL`.
Без этого поля Motion-only использует `BEZIER_EASE_IN_OUT`; его keyframes могут
иметь плавный разгон/торможение. Для старого поведения задайте его явно в своей
копии JSON. Поле `sequence_contract.edit_timebase_fps` относится к Motion-only,
а `sequence_contract.expected_edit_timebase_fps` — к insert+Motion.

Согласуйте `sequence_contract.expected_frames`, `dry_run.expected_output_frames`
и `review_export.expected_frames` с реальным числом кадров Motion-only.
В insert+Motion выход равен длительности основной sequence плюс `OUT - IN`
вставки. `resolved_destination_frame` должен попадать на границу picture item.

Review MP4 создаётся через FFmpeg, а не экспортом Adobe Premiere. Структурный QA
и совпадение числа кадров не заменяют открытия Save As-проекта в Premiere и
визуальной проверки Motion/effects.
