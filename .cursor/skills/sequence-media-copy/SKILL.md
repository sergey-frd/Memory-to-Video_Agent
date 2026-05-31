---
name: sequence-media-copy
description: Extract every source image (and optionally video) used by a Premiere `.prproj` sequence and copy the files into dedicated folders via `main_copy_sequence_media_batch.py` and `copy_sequence_media_*.json`. Use when the user wants to gather all images (or videos) referenced by a specific sequence, copy sequence media into an `input` folder for AI reprocessing, or run `run_copy_sequence_media_batch.bat`.
---

# Sequence Media Copy

Reads a Premiere Pro `.prproj`, collects every source media file actually used by a named sequence, de-duplicates the paths, and copies them into destination folders. Images and videos go to separate folders. Images are copied by default; video copying is opt-in.

## Two entry points

| Script | When to use |
| --- | --- |
| `main_copy_sequence_media_batch.py` | Config-driven run. Supports multiple sequences, separate image/video destinations, manifest. Mirrors `project_sequence_batch_*`. |
| `main_copy_sequence_images.py` | Quick images-only run via CLI args (`--project/--sequence/--dest`), no config file. |

The batch entry point has `.bat` wrappers: generic `run_copy_sequence_media_batch.bat <config.json>` and per-project, e.g. `copy_sequence_media_sveta_igr_26_2.bat`.

## How collection works

- The `.prproj` is gzip-XML; the auxiliary `.prin` is kept in config for reference only and is **not** parsed.
- All track groups and all clips of the requested sequence are walked (not just the primary visual track), via `utils/premiere_project.get_project_track_group_indexes()`.
- Each clip's source file is classified by extension: image (`.jpg .jpeg .png .webp .bmp .tif .tiff .heic`) or video (`.mp4 .mov .m4v .avi .mkv .webm .mts .m2ts`).
- Paths are de-duplicated; images copy to `image_dest`, videos to `video_dest`.
- Name conflicts default to `rename` (`name_1.jpg`); `overwrite` and `skip` are also available.
- A JSON manifest lists copied files and any missing sources.

## Config shape

`copy_sequence_media_<name>.json` (template: `copy_sequence_media_template.json`):

```json
{
  "project_path": "e:\\Git\\video_projects\\Sveta_I\\Svt_Igr_26_2.prproj",
  "prin_path": "e:\\Git\\video_projects\\Sveta_I\\Svt_Igr_26_2.prin",
  "image_dest": "e:\\Git\\P_h_o_t_o\\Igor_Brams_1\\Igor_Brams\\2026_Sv\\input",
  "video_dest": "e:\\Git\\P_h_o_t_o\\Igor_Brams_1\\Igor_Brams\\2026_Sv\\input_video",
  "copy_images": true,
  "copy_videos": false,
  "on_conflict": "rename",
  "dry_run": false,
  "manifest_path": "e:\\Git\\AI_PIC_DEF\\def_AI\\img-style-ag_1\\output\\Svt_Igr_26_2_media_copy_manifest.json",
  "sequence_jobs": [
    { "sequence_name": "Svt_Igr_262_e01" }
  ]
}
```

Quick semantics:

- `copy_images` (default `true`) routes images to `image_dest`.
- `copy_videos` (default `false`) is the opt-in video mode; it requires `video_dest`.
- `on_conflict` - `rename` | `overwrite` | `skip`.
- `dry_run: true` - count and report only, copy nothing (use to preview).
- `sequence_jobs` - one or more `{ "sequence_name": ... }`; `source_sequence_name` and a single `sequence` key are also accepted.
- `manifest_path` - optional; parent folders are created automatically.

## Launch examples

Run by config (preferred):

```bat
.\run_copy_sequence_media_batch.bat .\copy_sequence_media_sveta_igr_26_2.json
```

Pre-baked per-project wrapper:

```bat
.\copy_sequence_media_sveta_igr_26_2.bat
```

Direct Python call:

```powershell
python .\main_copy_sequence_media_batch.py --config .\copy_sequence_media_sveta_igr_26_2.json
```

Images-only quick mode (no config):

```powershell
python .\main_copy_sequence_images.py `
  --project "<PRPROJ>" --sequence "<SEQUENCE>" --dest "<IMAGE_DIR>" [--dry-run]
```

## Adding a new project copy job

1. Copy `copy_sequence_media_template.json` to `copy_sequence_media_<name>.json` and fill paths + `sequence_jobs`.
2. (Optional) add a wrapper `.bat` modeled on `copy_sequence_media_sveta_igr_26_2.bat` that `call`s `run_copy_sequence_media_batch.bat` with the config.
3. Preview with `"dry_run": true`, confirm the image/video counts, then set it back to `false`.

## Gotchas

| Symptom | Fix |
| --- | --- |
| `WinError 123 ... syntax is incorrect` with the dest path glued to `--manifest` | A trailing `\` in a `.bat` dest value escaped the closing quote. Remove the trailing backslash from `DEST`/`--dest`. |
| `Sequence '<name>' was not found` | The error lists available sequence names; copy the exact name into `sequence_jobs`. |
| Videos not copied | Set `copy_videos: true` **and** provide `video_dest`. |
| Some files reported as missing sources | Those clips point at media that no longer exists on disk; the run still copies everything else and records the misses in the manifest. |

## Reference

- `main_copy_sequence_media_batch.py` - config-driven entry point.
- `main_copy_sequence_images.py` - images-only CLI entry point.
- `utils/sequence_image_export.py` - collection, dedup, copy, and config runner.
- `utils/premiere_project.py` - `.prproj` parsing, `get_project_track_group_indexes()`, `is_supported_image_media_path` / `is_supported_video_media_path`.
- `copy_sequence_media_template.json` - config template.
- `docs/USER_GUIDE_RU.md` / `docs/USER_GUIDE_EN.md` -> "Copy Images (And Video) From A Sequence" - full prose.
