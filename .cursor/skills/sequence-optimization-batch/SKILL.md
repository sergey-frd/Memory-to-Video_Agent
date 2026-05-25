---
name: sequence-optimization-batch
description: Run the Premiere sequence optimization batch (`main_project_sequence_batch.py`) and rebuild reports after manual sequence edits (`main_sequence_reports.py`). Use when the user wants to reorder Premiere `.prproj` clips, generate transition/transform JSX, build music-first or human-profile reports, or rebuild reports after manually editing an optimized sequence.
---

# Sequence Optimization Batch

Reorders clips inside a Premiere Pro `.prproj`, optionally writes companion JSX scripts for transitions and still-image transforms, and produces a full report bundle (structure, transitions, music-first, human profile).

## Two entry points

| Script | When to use |
| --- | --- |
| `main_project_sequence_batch.py` | Full batch run: read source `.prproj`, optimize, export a new `.prproj`, write reports + JSX. |
| `main_sequence_reports.py` | Rebuild reports for an already-finalized sequence after manual edits. No reordering. |

The batch entry point also has `.bat` wrappers per project, e.g. `run_project_sequence_batch_igor_26_1A.bat`, `run_project_sequence_batch_nicol_26_T2.bat`, `run_project_sequence_batch_vika_26_1A.bat`. The generic one is `run_project_sequence_batch.bat`.

## Batch config shape

Required keys (`project_sequence_batch_<name>.json`):

```json
{
  "project_path": "E:\\Git\\video_projects\\Igor\\Proj\\Igor26_1A_w01.prproj",
  "regeneration_assets_dir": "E:\\Git\\P_h_o_t_o\\Igor_Brams_1\\Igor_Brams\\2026\\regeneration_assets",
  "output_project_path": "E:\\Git\\video_projects\\Igor\\Proj\\Igor26_1A_o01.prproj",
  "reports_dir": "E:\\Git\\P_h_o_t_o\\Igor_Brams_1\\Igor_Brams\\2026\\reports",
  "transition_mode": "apply",
  "enable_auto_transitions": true,
  "enable_visual_transitions": true,
  "enable_auto_durations": true,
  "enable_auto_transforms": true,
  "generate_premiere_transform_script": true,
  "premiere_transform_script_add_video_effects": true,
  "include_visual_media": true,
  "generate_personalized_report": false,
  "human_detail_txt": "E:\\Git\\AI_PIC_DEF\\def\\Detail_1\\Igor\\Igor_detail.txt",
  "sequence_jobs": [
    { "source_sequence_name": "Igor26_baby_1_e01", "new_sequence_name": "Igor26_baby_1_o01" }
  ]
}
```

Quick semantics:

- `include_visual_media` - source sequence mixes photos + videos on one visual track.
- `enable_auto_durations` - optimizer adjusts timeline durations.
- `transition_mode: "apply"` + `enable_auto_transitions` + `enable_visual_transitions` - export `.prproj` with automatic transitions for mixed visual pairs (not only pure `mp4` pairs). `Morph Cut` is intentionally excluded from auto-apply.
- `enable_auto_transforms` + `generate_premiere_transform_script: true` - produces `<sequence>_apply_transforms.jsx` with `Grow`/`Shrink`/`Move` Transform effects for still images. `Offset` stays manual-only.
- `premiere_transform_script_add_video_effects: true` - the JSX applies named Premiere Transform effects and skips intrinsic Motion > Scale keyframes.
- `generate_personalized_report: true` + `human_detail_txt` - adds a human-profile report next to the standard ones.

## Launch examples

Run by config (preferred — keep a per-project config next to the template):

```bat
.\run_project_sequence_batch.bat .\project_sequence_batch_igor_26_1A.json
```

Direct Python call:

```powershell
python .\main_project_sequence_batch.py --config .\project_sequence_batch_igor_26_1A.json
```

Pre-baked wrappers for specific projects:

```bat
.\run_project_sequence_batch_igor_26_1A.bat
.\run_project_sequence_batch_nicol_26_T2.bat
.\run_project_sequence_batch_vika_26_1A.bat
```

## After the batch

The final optimized `.prproj` is written next to the source `project_path`. A temporary working copy stays in `reports/temp_projects/` and may be removed later by the cleanup tool. The `reports/` folder gains:

- `batch_summary.json` + `batch_summary.txt`
- `batch_transition_recommendations.txt`
- per-sequence JSON/TXT reports
- `<sequence>_structure.txt`
- `<sequence>_transition_recommendations.txt`
- `<sequence>_human_profile_report.txt` (only when personalized report is enabled)

Open the optimized `.prproj` in Premiere. To apply the companion transforms, open the optimized sequence and run `<sequence>_apply_transforms.jsx` from the same Premiere panel used for transition scripts.

## Rebuild reports after manual edits

If you adjusted the optimized sequence by hand and now want fresh reports for the approved order:

```powershell
python .\main_sequence_reports.py `
  --prproj "E:\Git\video_projects\Igor\Proj\Igor26_1A_o01.prproj" `
  --sequence-name "Igor26_baby_1_o01" `
  --optimization-report-json "E:\Git\P_h_o_t_o\Igor_Brams_1\Igor_Brams\2026\reports\01_Igor26_baby_1_o01.json" `
  --output-dir "E:\Git\P_h_o_t_o\Igor_Brams_1\Igor_Brams\2026\reports"
```

This produces `<sequence>_manual_order.json`, `<sequence>_manual_order_music.txt`, `<sequence>_manual_order_structure.txt`, and `<sequence>_manual_order_transition_recommendations.txt`. Music-first comes first in this output.

`main_sequence_music_first.py` is the lighter sibling when you only need the music recommendation.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Can't apply to a single clip` from Premiere | Likely Morph Cut auto-applied. Confirm `transition_mode` excludes Morph Cut (the default does). |
| Optimized `.prproj` written to the wrong folder | Older configs may point `output_project_path` into `reports`. The file name is preserved but the persistent copy still lands next to `project_path`. Update the config to be explicit. |
| Reports missing the human-profile section | `generate_personalized_report: true` + valid `human_detail_txt` path must both be set. |
| Manual rebuild loses original suggestions | Pass the original `--optimization-report-json` so the rebuilt report can carry over context. |

## Reference

- `main_project_sequence_batch.py` - batch entry point.
- `main_sequence_reports.py` - manual-order rebuild entry point.
- `main_sequence_music_first.py` - music-only report.
- `utils/sequence_optimizer*.py`, `utils/premiere_xml.py`, `utils/premiere_project.py` - optimizer internals.
- `utils/premiere_transition_script.py`, `utils/premiere_transform_script.py` - JSX generators.
- `styles/List of Video transform effects.txt` - Transform effect inventory.
- `USER_GUIDE.md` -> "Sequence Optimization Batch" / "Rebuild Reports..." - full prose.
