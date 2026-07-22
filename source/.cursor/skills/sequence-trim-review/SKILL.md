---
name: sequence-trim-review
description: Run Premiere Sequence Trim Review (`main_sequence_trim_review.py`) to split raw timeline clips into compact KEEP/DROP segments with heuristic and semantic engines, then export a review `.prproj`. Use when the user wants to shorten raw footage, compare heuristic vs meaning-based cuts, or place KEEP on V1 and DROP on V2.
---

# Sequence Trim Review

Reads a Premiere `.prproj` sequence of raw/visual clips, proposes **per-clip KEEP/DROP segments**, and writes a review project where KEEP stays on V1 and DROP moves to V2. User deletes DROP manually.

## Engines

| Engine | What it does |
| --- | --- |
| `heuristic` | Budget + position/length rules; compact still/video keep islands |
| `semantic` | Extracts frames (ffmpeg / `imageio-ffmpeg`) and asks OpenAI vision for the shortest meaningful keep window |

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
- `context_notes`: story intent for the semantic engine
- `split_tracks`, `keep_track_index`, `drop_track_index`

## After the run

1. Open the output `.prproj`.
2. Compare `*_trim_heuristic` vs `*_trim_semantic`.
3. Mute V2 to preview KEEP-only.
4. Delete DROP segments yourself.

Reports land in `reports_dir` as a bundle JSON/TXT plus per-engine reports.

## Reference

- `main_sequence_trim_review.py`
- `utils/sequence_trim_review.py`, `utils/sequence_trim_classifier.py`, `utils/sequence_trim_semantic.py`
- `utils/premiere_trim_review_export.py`, `utils/video_frame_extract.py`
- `api/openai_trim_semantic.py`
- `models/sequence_trim_review.py`
- `test/test_sequence_trim_review_app.py`
- `USER_GUIDE.md` → Sequence Trim Review
