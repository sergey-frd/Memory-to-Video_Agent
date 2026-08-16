---
name: sequence-trim-review
description: Run Premiere Sequence Trim Review (`main_sequence_trim_review.py`) to split raw timeline clips into compact KEEP/DROP segments with heuristic and semantic engines, then export a review `.prproj`. Also covers `apply_keep_ranges` via `run_sequence_keep_apply.bat`, media import, and `import_and_keep` (import then KEEP/cleaning in one pass). Use when the user wants to shorten raw footage, compare heuristic vs meaning-based cuts, place KEEP on V1 and DROP on V2, apply a manual KEEP JSON, import listed files onto a sequence, or run import and keep together.
---

# Sequence Trim Review

Reads a Premiere `.prproj` sequence of raw/visual clips, proposes **per-clip KEEP/DROP segments**, and writes a review project where KEEP stays on V1 and DROP moves to V2. User deletes DROP manually.

A separate mode, `apply_keep_ranges`, copies the whole project and physically removes unused pieces of listed media files according to a manual KEEP JSON. Unlisted clips stay. Linked audio is trimmed with the video.

`import_media` appends media to a sequence. `files` looks up exact filenames under `root_directory` (or `relative_path`). `items` uses absolute `source_path` plus `order` and does not search by name. Empty `sequence_name` uses the first non-`lib` sequence. Empty projects borrow a clip template from `template_project_path` or a sibling `.prproj`. Each imported file gets its own Premiere `MasterClip` and its own `VideoStream`/`AudioStream`. `import_and_keep` runs import and then KEEP/cleaning in one pass.

## Engines

| Engine | What it does |
| --- | --- |
| `heuristic` | Budget + position/length rules; compact still/video keep islands |
| `semantic` | Extracts frames (ffmpeg / `imageio-ffmpeg`) and asks OpenAI vision for the shortest meaningful keep window |
| `hero` | Compares sampled frames with `hero_def.json`; keeps HIGH/MEDIUM hero appearances plus configurable context |

Default config runs **both** into one output `.prproj` as two sequences.

## Compact keep

When `compact_keep: true` (default):

- still images / photos: KEEP about **1.5–3.0s**
- video takes: KEEP islands about **2.0–8.0s** (catch the point, do not keep long fluff)

## Launch

```bat
.\run_sequence_trim_review.bat .\sequence_trim_review_01.json
```

```powershell
python .\main_sequence_trim_review.py --config .\sequence_trim_review_01.json
```

Template: `sequence_trim_review_template.json`.

## Important config keys

- `engines`: `["heuristic", "semantic"]`
- `new_sequence_name_heuristic` / `new_sequence_name_semantic`
- `compact_keep`, `photo_keep_min_seconds`, `photo_keep_max_seconds`, `video_keep_min_seconds`, `video_keep_max_seconds`
- `semantic_frames_per_clip`, `semantic_model` (needs `OPENAI_API_KEY`)
- `hero_definition_path`, `hero_match_model`, `hero_frame_interval_seconds`
- `hero_pre_roll_seconds`, `hero_post_roll_seconds`, `hero_keep_medium_matches`
- `context_notes`: story intent for the semantic engine
- `split_tracks`, `keep_track_index`, `drop_track_index`

## After the run

1. Open the output `.prproj`.
2. Compare `*_trim_heuristic` vs `*_trim_semantic`, or inspect `[KEEP-HIGH]` / `[KEEP-MEDIUM]` in a hero sequence.
3. Mute V2 to preview KEEP-only.
4. Delete DROP segments yourself.

Reports land in `reports_dir` as a bundle JSON/TXT plus per-engine reports.

## Apply a manual KEEP JSON

When keep windows are already known (source timecode, not timeline position). Preferred launcher:

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_2_min.json
```

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_2_min_vtr_2.json
```

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_apply_template.json
```

The same JSON also works through the shared trim-review launcher:

```bat
.\run_sequence_trim_review.bat .\sequence_keep_apply_yotam26_2_min.json
```

```powershell
python .\main_sequence_trim_review.py --config .\sequence_keep_apply_yotam26_2_min.json
```

Config keys:

- `mode`: `apply_keep_ranges` (optional when the JSON has `operations`)
- `project_path` in the wrapper or inside the KEEP JSON
- `sequence_name` or `source_sequence_name` (empty = all named sequences)
- `keep_ranges_path`, inline `operations`, or inline `clips`
- optional `prin_path` (reference only)
- `output_project_path`, `ripple_compact` (default `true`)

New KEEP JSON shape (`10_Yotam_minimal_agent_trim_2.json`):

```json
{
  "project_path": "<LOCAL_PATH>",
  "sequence_name": "Yotam26_2_min_vtr_2",
  "operations": [
    {
      "file": "IMG_5104_3.mp4",
      "keep_ranges": [
        {"in": "00:00:00.350", "out": "00:00:02.300"},
        {"in": "00:00:10.000", "out": "00:00:12.000"}
      ]
    }
  ]
}
```

Keep ranges are source timecode of the media file. A range outside the current In/Out is restored from the original file. Stills may use `"duration"` instead of `keep_ranges`. If the named `.prproj` has no visual clips, keep-apply uses a sibling `*_import.prproj`. The older `clips` / `keep` list still works.

The source `.prproj` is not modified. Open the new project and review the same sequence names with shorter listed clips.

## Import listed files into a sequence

Look up filenames under `root_directory` and append them to a sequence. Already imported media is reused; new files are added as new Media and MasterClip objects. The source `.prproj` is not modified.

```bat
.\run_sequence_media_import.bat .\sequence_media_import_yotam26_part2.json
```

```bat
.\run_sequence_media_import.bat <LOCAL_PATH>
```

```json
{
  "project_path": "<LOCAL_PATH>",
  "sequence_name": "Yotam26_20_v01",
  "create_sequence_if_missing": true,
  "root_directory": "<LOCAL_PATH>",
  "files": ["IMG_4531.MP4", "IMG_4793.jpg"]
}
```

## Import and keep in one pass

`mode: "import_and_keep"` runs `import_media` and then `apply_keep_ranges` on the imported project. The KEEP JSON `project_path` is ignored.

```bat
.\run_sequence_import_and_keep.bat <LOCAL_PATH>
```

```json
{
  "mode": "import_and_keep",
  "import_path": "<LOCAL_PATH>",
  "keep_ranges_path": "<LOCAL_PATH>",
  "output_project_path": "<LOCAL_PATH>"
}
```

## Replay without OpenAI

Set `"mode": "report_replay"` and point `review_json_path` to an existing hero per-engine JSON report. Running the normal CLI then exports one aligned sequence with HIGH, MEDIUM, REVIEW, and DROP on four separate video tracks, without frame extraction or API requests.

```bat
.\run_sequence_trim_review.bat .\sequence_trim_review_Alice_replay_levels.json
```

## Reference

- `main_sequence_trim_review.py`
- `utils/sequence_trim_review.py`, `utils/sequence_trim_classifier.py`, `utils/sequence_trim_semantic.py`
- `utils/sequence_keep_apply.py`, `utils/premiere_keep_apply_export.py`
- `utils/sequence_media_import.py`, `utils/premiere_media_import_export.py`
- `utils/sequence_import_and_keep.py`
- `utils/premiere_trim_review_export.py`, `utils/video_frame_extract.py`
- `api/openai_trim_semantic.py`
- `models/sequence_trim_review.py`, `models/sequence_media_import.py`
- `run_sequence_keep_apply.bat`, `run_sequence_media_import.bat`, `run_sequence_import_and_keep.bat`, `run_sequence_trim_review.bat`
- `test/test_sequence_trim_review_app.py`, `test/test_sequence_keep_apply.py`, `test/test_sequence_media_import.py`, `test/test_sequence_import_and_keep.py`
- `docs/USER_GUIDE_EN.md` / `docs/USER_GUIDE_RU.md` → Sequence Trim Review
- `docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md` → sections 4–5
- `docs/BATCH_RUN_HISTORY.md` → B037–B041
