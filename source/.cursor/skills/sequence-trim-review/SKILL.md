---
name: sequence-trim-review
description: Run Premiere Sequence Trim Review (`main_sequence_trim_review.py`) to split raw timeline clips into compact KEEP/DROP segments with heuristic and semantic engines, then export a review `.prproj`. Also covers `apply_keep_ranges` / `keep_to_new_sequence` via `run_sequence_keep_apply.bat`, media import / `import_to_new_sequence`, and `import_and_keep`. Use when the user wants to shorten raw footage, compare heuristic vs meaning-based cuts, place KEEP on V1 and DROP on V2, apply a manual KEEP JSON, import listed files onto a sequence, create a new sequence in the existing `.prproj`, copy a source sequence and KEEP-trim the copy, or run import and keep together.
---

# Sequence Trim Review

Reads a Premiere `.prproj` sequence of raw/visual clips, proposes **per-clip KEEP/DROP segments**, and writes a review project where KEEP stays on V1 and DROP moves to V2. User deletes DROP manually.

A separate mode, `apply_keep_ranges`, copies the whole project and physically removes unused pieces of listed media files according to a manual KEEP JSON. Unlisted clips stay. Linked audio is trimmed with the video.

`keep_to_new_sequence` copies the named source sequence to a new output sequence inside the **same** `.prproj` and applies KEEP only to the copy. The source sequence is left unchanged. No extra `.prproj` is created unless `output_project_path` is set.

`import_media` appends media to a sequence and writes a sibling `*_import.prproj`. `import_to_new_sequence` creates a new empty sequence in the existing project and imports there; other sequences are not modified. `files` looks up exact filenames under `root_directory` (or `relative_path`). `items` uses absolute `source_path` plus `order` and does not search by name. Empty `sequence_name` uses the first non-`lib` sequence except in `import_to_new_sequence`, which requires `output_sequence_name`. Empty projects use `template_project_path` or a sibling `.prproj` as the project base instead of grafting clips into an empty Premiere stub; a new sequence is cloned from that base. Donor-only bin metadata such as `SecondaryContentItem` is dropped if the referenced object is not in the working project. Each imported file gets its own Premiere `MasterClip` and its own `VideoStream`/`AudioStream`, including files that share a filename but live in different folders. Extra template effects are stripped; Motion is reset to a centered 100% still. `import_and_keep` runs import and then KEEP/cleaning in one pass.

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

Import/keep dedicated runner (preferred for those JSON configs):

```bat
.\run_sequence_media_import.bat .\sequence_media_import_template.json
.\run_sequence_keep_apply.bat .\sequence_keep_apply_template.json
.\run_sequence_import_and_keep.bat .\sequence_import_and_keep_template.json
```

Portable aliases from the ChatGPT Work package call the same runner:

```bat
.\run_sequence_media_import_standalone.bat .\sequence_media_import_template.json
.\run_sequence_keep_apply_standalone.bat .\sequence_keep_apply_template.json
.\run_sequence_import_and_keep_standalone.bat .\sequence_import_and_keep_template.json
```

```powershell
python .\main_premiere_import_keep.py --config .\sequence_media_import_template.json
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
.\run_sequence_keep_apply_standalone.bat .\sequence_keep_apply_template.json
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
python .\main_premiere_import_keep.py --config .\sequence_keep_apply_yotam26_2_min.json
python .\main_sequence_trim_review.py --config .\sequence_keep_apply_yotam26_2_min.json
```

Config keys:

- `mode`: `apply_keep_ranges` or `keep_to_new_sequence` (optional when the JSON has `operations`)
- `project_path` in the wrapper or inside the KEEP JSON
- `sequence_name` or `source_sequence_name` (empty = all named sequences)
- `output_sequence_name` for `keep_to_new_sequence`
- `create_output_sequence_from_source`, `preserve_source_sequence`, `fail_if_output_sequence_exists` (all default `true` in `keep_to_new_sequence`)
- `keep_ranges_path`, inline `operations`, or inline `clips`
- optional `prin_path` (reference only)
- `output_project_path` (default `*_keep.prproj`; same `project_path` for `keep_to_new_sequence`), `ripple_compact` (default `true`)

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

Keep ranges are source timecode of the media file. A range outside the current In/Out is restored from the original file. Stills may use `"duration"` instead of `keep_ranges`. Operations may identify a file with `file` (basename) or with `source_path` (full media path). Duplicate filenames are allowed when each copy has its own `source_path`. The same `source_path` may be listed more than once when each copy has a unique `order` matching the timeline clip index (1-based). If the named `.prproj` has no visual clips, `apply_keep_ranges` uses a sibling `*_import.prproj`. The older `clips` / `keep` list still works.

`apply_keep_ranges` does not modify the source `.prproj`. Open the new project and review the same sequence names with shorter listed clips.

`keep_to_new_sequence` writes the same `.prproj`: it copies `source_sequence_name` to `output_sequence_name` and KEEP-trims only the copy. Close Premiere (or do not save over the file) before the in-place write.

Repo template: `sequence_keep_to_new_sequence_template.json`. Compact Yotam example: `sequence_keep_apply_yotam26_macro_styles.json`. Full 74-file job: `Yotam_macro_styles_keep_v02.json`.

```bat
.\run_sequence_keep_apply.bat .\sequence_keep_to_new_sequence_template.json
.\run_sequence_keep_apply.bat .\sequence_keep_apply_yotam26_macro_styles.json
.\run_sequence_keep_apply.bat <LOCAL_PATH>
```

```json
{
  "mode": "keep_to_new_sequence",
  "project_path": "<LOCAL_PATH>",
  "source_sequence_name": "Yt_macro_styles_IMPORT_v01",
  "output_sequence_name": "Yt_macro_styles_KEEP_v01",
  "create_output_sequence_from_source": true,
  "preserve_source_sequence": true,
  "fail_if_output_sequence_exists": true,
  "write_project": true,
  "operations": [
    {"order": 1, "source_path": "<LOCAL_PATH>", "duration": "00:00:0.800"},
    {"order": 2, "source_path": "<LOCAL_PATH>", "duration": "00:00:1.100"}
  ]
}
```

## Import listed files into a sequence

Look up filenames under `root_directory` and append them to a sequence. Media already in the project is reused only when the **full path** matches; the same filename in another folder gets a new `MasterClip`. `import_media` writes a sibling `*_import.prproj` and does not modify the source file.

```bat
.\run_sequence_media_import.bat .\sequence_media_import_yotam26_part2.json
```

```bat
.\run_sequence_media_import_standalone.bat .\sequence_media_import_template.json
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

`import_to_new_sequence` creates `output_sequence_name` inside the existing `.prproj` and imports there. Other sequences stay unchanged. `fail_if_sequence_exists` (default `true` in this mode) refuses to append if the name is already taken. Close Premiere before the in-place write.

If `source_path` is missing, import also tries `__`↔`_` in the same folder, then a unique `rglob` under the nearest existing parent. Items may use `source_name` with `root_search_paths` (or `root_directory`) instead of an absolute path.

Repo template: `sequence_media_import_to_new_sequence_template.json`. Compact Yotam example: `sequence_media_import_yotam26_macro_styles.json`. Full 74-file job: `Yotam_macro_styles_import_v02.json`.

```bat
.\run_sequence_media_import.bat .\sequence_media_import_to_new_sequence_template.json
.\run_sequence_media_import.bat .\sequence_media_import_yotam26_macro_styles.json
.\run_sequence_media_import.bat <LOCAL_PATH>
```

```json
{
  "mode": "import_to_new_sequence",
  "project_path": "<LOCAL_PATH>",
  "output_sequence_name": "Yt_macro_styles_IMPORT_v01",
  "create_sequence_if_missing": true,
  "fail_if_sequence_exists": true,
  "write_project": true,
  "items": [
    {"order": 1, "source_path": "<LOCAL_PATH>"},
    {"order": 2, "source_path": "<LOCAL_PATH>"}
  ]
}
```

## Import and keep in one pass

`mode: "import_and_keep"` runs `import_media` and then `apply_keep_ranges` on the imported project. The KEEP JSON `project_path` is ignored.

```bat
.\run_sequence_import_and_keep.bat <LOCAL_PATH>
```

```bat
.\run_sequence_import_and_keep_standalone.bat .\sequence_import_and_keep_template.json
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
- `main_premiere_import_keep.py`
- `utils/sequence_trim_review.py`, `utils/sequence_trim_classifier.py`, `utils/sequence_trim_semantic.py`
- `utils/premiere_project_export.py` (`clone_named_sequence`)
- `utils/sequence_keep_apply.py`, `utils/premiere_keep_apply_export.py`
- `utils/sequence_media_import.py`, `utils/premiere_media_import_export.py`
- `utils/sequence_import_and_keep.py`
- `utils/premiere_trim_review_export.py`, `utils/video_frame_extract.py`
- `api/openai_trim_semantic.py`
- `models/sequence_trim_review.py`, `models/sequence_keep_apply.py`, `models/sequence_media_import.py`
- `sequence_media_import_template.json`, `sequence_media_import_to_new_sequence_template.json`, `sequence_media_import_yotam26_macro_styles.json`
- `sequence_keep_apply_template.json`, `sequence_keep_to_new_sequence_template.json`, `sequence_keep_apply_yotam26_macro_styles.json`
- `run_sequence_keep_apply.bat`, `run_sequence_media_import.bat`, `run_sequence_import_and_keep.bat`, `run_sequence_trim_review.bat`
- `run_sequence_keep_apply_standalone.bat`, `run_sequence_media_import_standalone.bat`, `run_sequence_import_and_keep_standalone.bat`
- `test/test_sequence_trim_review_app.py`, `test/test_sequence_keep_apply.py`, `test/test_sequence_media_import.py`, `test/test_sequence_import_and_keep.py`, `test/test_premiere_import_keep_main.py`
- `docs/USER_GUIDE_EN.md` / `docs/USER_GUIDE_RU.md` → Sequence Trim Review
- `docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md` → sections 4–5
- `docs/BATCH_RUN_HISTORY.md` → B037–B044, B065–B067
