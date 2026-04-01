# User Guide

## One-Minute Quick Start

1. Put source images into `input`.
2. If you need to sign in to Grok or refresh the session, run `login_grok_profile.bat`, sign in, open `https://grok.com/imagine` once to verify access, and then fully close that Chrome window.
3. Run the main pipeline:

```bat
run_full_grok_pipeline.bat --upload-timeout 300
```

4. After a successful run:
   - final `mp4` files and background images will be copied to `final_videos_dir`;
   - prompt files, manifests, and other non-video artifacts will be copied to `regeneration_assets_dir`.
5. If a stage fails, the problematic files will be moved to `error\input` and `error\output`.
6. After the video generation phase, build Premiere sequences manually from the generated videos.
7. Run sequence optimization and open the final optimized `.prproj` from `reports`, not from `output`.
8. If you manually adjust the optimized sequence, rebuild reports from the current sequence order with `main_sequence_reports.py`.

## Purpose

This project is used to prepare prompt files, generate background images and videos through Grok, optimize Premiere sequence order, and build reports for the final editing phase. The main workflow is: input image -> generate media -> build manual Premiere sequences -> optimize sequence order -> manually refine -> rebuild final recommendations from the approved order.

## Main Directories

- `input` - source images for the current run.
- `output` - temporary prompt files, manifest files, and intermediate results for the current stage.
- `final_videos_dir` - final destination for generated `mp4` files and background images.
- `regeneration_assets_dir` - destination for prompt files, manifests, and other non-video artifacts needed for manual editing and regeneration.
- `reports` - final destination for optimized `.prproj` files and all sequence optimization reports.
- `reports\temp_projects` - intermediate `.prproj` files produced inside one sequence optimization batch.
- `error\input` - source images for stages that failed.
- `error\output` - prompt files, manifests, and error reports for failed stages.
- `.browser-profile\grok-web` - Chrome automation profile used for Grok.

Example Windows paths in `config.json`:

```json
{
  "final_videos_dir": "<LOCAL_PATH>",
  "regeneration_assets_dir": "<LOCAL_PATH>",
  "reports_dir": "<LOCAL_PATH>"
}
```

## BAT Files

### `login_grok_profile.bat`

Purpose:
- open Chrome with the project automation profile;
- sign in to Grok manually;
- verify that `https://grok.com/imagine` opens successfully.

When to use it:
- on the first run;
- if Grok signed out;
- if Grok starts showing `Sign in` or `Sign up`.

Important:
- this bat file is only for manual login;
- after checking access, close that Chrome window completely;
- the main pipeline starts Grok on its own when it runs.

### `run_grok_automation.bat`

Purpose:
- run Grok for one image / one prompt file.

Example:

```bat
run_grok_automation.bat --image .\input\photo.jpg --prompt .\output\photo_20260314_101010_v_prompt_1.txt --upload-timeout 300
```

Useful when you want to:
- re-run one prompt;
- regenerate only one background or one video;
- test a single Grok stage without running the full pipeline.

### `run_grok_automation_all.bat`

Purpose:
- process all `*_v_prompt_*.txt` files already present in `output`.

Examples:

```bat
run_grok_automation_all.bat --upload-timeout 300
run_grok_automation_all.bat --skip-existing --upload-timeout 300
run_grok_automation_all.bat --skip-video --generate-source-background --upload-timeout 300
```

This bat file is useful when prompt files already exist and you only need the Grok part.

### `run_full_grok_pipeline.bat`

This is the main launcher for normal operation.

It does the following:
1. takes one input image from `input`;
2. builds all stage files in `output`;
3. starts Grok for that image;
4. saves the background image and/or video;
5. copies results to `final_videos_dir` and `regeneration_assets_dir`;
6. closes Grok;
7. continues with the next image.

Examples:

```bat
run_full_grok_pipeline.bat --upload-timeout 300
run_full_grok_pipeline.bat --skip-video --generate-source-background --upload-timeout 300
run_full_grok_pipeline.bat --save-grok-debug-artifacts --upload-timeout 300
```

## New Generation Flags

### `generate_video`

Controls video generation. Default: `true`.

In `config.json`:

```json
{
  "generate_video": true
}
```

CLI parameters:

```bat
--generate-video
--skip-video
```

Behavior:
- if `generate_video = true`, the pipeline generates videos in Grok;
- if `generate_video = false` and `generate_source_background = true`, the pipeline generates only background images;
- if `generate_video = false` and `generate_source_background = false`, the stage fails because there is nothing to generate.

When visible people are present, `*_v_prompt_*.txt` and `*_v_prm_ru_*.txt` now prefer identity-safe camera language: more distant or medium-wide framing, side / top / low / drone-like angles, and less aggressive facial enlargement. The goal is to reduce face drift in generated videos.

This identity-safe behavior is now the default, but it is configurable through three optional JSON flags:

```json
{
  "prefer_face_closeups": false,
  "use_ai_optimal_framing": false,
  "generate_dual_framing_videos": false
}
```

Framing mode rules:
- if all three flags are `false`, the pipeline keeps the default identity-safe framing and tries to avoid aggressive face enlargement;
- if `prefer_face_closeups = true`, close facial framing may be preferred and the video prompt may move into a tighter portrait scale;
- if `use_ai_optimal_framing = true`, the AI chooses the most effective cinematic framing for the source image even if that includes stronger facial emphasis;
- if `generate_dual_framing_videos = true`, the pipeline builds two branches from the same source frame: one identity-safe branch and one AI-optimal branch.

Dual-mode output count:
- with `video_count = 1`, dual mode produces `2` videos;
- with `video_count = N`, dual mode produces `2 x N` videos.

Only one of these three framing flags can be enabled at a time.

When to use `--skip-video`:
- when you only need background images;
- when you want to postpone video generation;
- when you want to prepare backgrounds first and generate videos later.

### `generate_source_background`

Controls background image generation in Grok.

CLI parameters:

```bat
--generate-source-background
--skip-source-background
```

Current behavior:
- background generation uses `*_assoc_bg_prompt.txt`;
- that descriptor describes a realistic associative image suitable as a background;
- Grok builds a new background from that descriptor and uses the source image as visual guidance.

### `save_grok_debug_artifacts`

Controls whether Grok diagnostic files are kept. Default: `false`.

In `config.json`:

```json
{
  "save_grok_debug_artifacts": false
}
```

CLI parameters:

```bat
--save-grok-debug-artifacts
--skip-grok-debug-artifacts
```

Behavior:
- if `false`, candidate/debug files do not remain in `output`, so the working folder stays clean;
- if `true`, Grok diagnostic artifacts are saved in `output`.

Possible files when enabled:
- `*_bg_image_16x9.candidate_*.png`
- `*_bg_image_16x9_candidates.json`
- `*_grok_debug.png`
- `*_grok_debug.html`
- `*_grok_debug.json`

When to enable it:
- if Grok saved the wrong background image;
- if you need to see which candidate was found on the page;
- if you need detailed diagnostics of Grok page results.

When to keep it disabled:
- during normal operation;
- when you want `output` to stay clean.

### `continue_after_failure`

Controls what happens after a failed stage.

Behavior:
- if `false`, the pipeline stops on the first failure;
- if `true`, the failed stage is moved to `error`, and processing continues with the next image.

When to enable it:
- when there are many input images;
- when some of them may be too large or otherwise problematic;
- when it is more convenient to review only failed stages later.

## Full Generation Config Reference

All current `GenerationConfig` fields:

- `generate_video` — default `true`; generate video prompts and run the video stage.
- `video_count` — default `2`; how many videos to build from one source frame for each active framing mode.
- `camera_segments` — default `1`; how many motion segments are planned inside one video prompt.
- `motion_source` — default `table`; choose camera motions from the local table or from AI (`ai`).
- `motion_model` — default `gpt-4.1`; OpenAI model used for AI motion selection when `motion_source = ai`.
- `generate_source_background` — default `false`; create background prompts and run the background-image stage in Grok.
- `save_grok_debug_artifacts` — default `false`; keep Grok diagnostic candidate/debug artifacts in `output`.
- `final_videos_dir` — default `final_project/videos`; final delivery folder for generated `mp4` files and background images.
- `regeneration_assets_dir` — default `final_project/regeneration_assets`; delivery folder for prompts, manifests, and non-video stage artifacts.
- `continue_after_failure` — default `false`; continue with the next image after moving a failed stage into `error`.
- `write_description` — default `true`; write the stage description / analysis text file.
- `generate_final_frames` — default `false`; generate final-frame images through the image API.
- `read_input_list` — default `true`; read all supported source images from `input`.
- `generate_music` — default `false`; generate a music prompt after the last processed image.
- `prefer_face_closeups` — default `false`; prefer closer facial framing when that matches the source image.
- `use_ai_optimal_framing` — default `false`; let AI choose the strongest cinematic framing, even if faces become more prominent.
- `generate_dual_framing_videos` — default `false`; generate both identity-safe and AI-optimal framing branches from the same source frame.
- `hide_phone_in_selfie` — default `true`; if the input looks like a selfie / self-portrait, keep the selfie feel but try not to show the phone or phone reflection when plausible.
- `prefer_loving_kindness_tone` — default `false`; where appropriate for the specific input image, gently bias the prompts toward loving-kindness, friendliness, benevolence, warm goodwill, and gentle mercy through light, color, atmosphere, environment, and background.

Important framing rule:
- Only one of `prefer_face_closeups`, `use_ai_optimal_framing`, or `generate_dual_framing_videos` can be enabled at a time.

## Architecture And Change Control

For a developer-oriented map of the project structure, data flows, invariants, and change-impact checklist, see `PROJECT_STRUCTURE.md`.
For automation and machine-guided change review, see `project_structure_registry.json`.
Use `python .\main_change_impact.py --change-type generation_flag --changed-file config.py` to generate a concrete impact checklist.
Use `python .\main_project_publication.py --target-dir .\project_publication\Memory-to-Video_Agent` to refresh the external project-information repository bundle.
The public bundle now includes a limited code snapshot: `code/main.py`, `code/config.py`, and `code/config.json`.
Use `python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --stage` for the guarded public-repo publish flow.
Use `.\run_project_publication_stage.bat` for the shortest preview/stage-only run without push.
Use `.\run_project_publication_push.bat` for the shortest manual publish command with your current local clone path.

## What Gets Copied After a Successful Stage

Into `final_videos_dir`:
- `*.mp4`;
- final background images.

Into `regeneration_assets_dir\<stage_id>`:
- `description`;
- `scene_analysis`;
- `v_prompt`;
- `v_prm_ru`;
- `bg_prompt`;
- `bg_prm_ru`;
- `assoc_bg_prompt`;
- `assoc_bg_prm_ru`;
- `manifest`;
- other non-video stage files.

Not copied to `regeneration_assets_dir`:
- the source image;
- final `mp4` files;
- the final background image.

## What Happens on Failure

If a stage fails:
- stage files from `output` are moved to `error\output\<stage_id>`;
- the source image is moved to `error\input\<stage_id>`;
- a file named `<stage_id>_error.txt` is saved next to them with the error details.

This makes it easier to inspect and re-run only the problematic images.

If Grok closes the current browser tab right after `submit`, the automation now tries to recover from another live Grok tab or from a just-finished download before marking the stage as failed.
For unfinished stages that already have a ready `*_v_prompt_*.txt`, you can safely re-run only the video step and skip repeated background generation.

## Premiere Sequence Workflow

After video generation is finished, the normal process is:

1. Build Premiere sequences manually from the generated `mp4` files.
2. Run the sequence optimization batch to create new `_oNN` sequences from approved `_eNN` sequences.
3. Review the optimized result in Premiere.
4. If needed, manually change clip order again after optimization.
5. Rebuild the reports from the current manual order.
6. Keep the final result in `reports`.

Important location rule:

- `reports` is the final result for sequence optimization and reporting.
- `output` is a temporary workspace area.
- If everything finishes successfully, `output` should ideally end up empty.

## Sequence Optimization Batch

Run the batch optimizer with a JSON config:

```powershell
python .\main_project_sequence_batch.py --config .\project_sequence_batch_igor_26_1A.json
```

Example config fields:

```json
{
  "project_path": "<LOCAL_PATH>",
  "regeneration_assets_dir": "<LOCAL_PATH>",
  "output_project_path": "<LOCAL_PATH>",
  "reports_dir": "<LOCAL_PATH>",
  "generate_personalized_report": false,
  "human_detail_txt": "<LOCAL_PATH>",
  "sequence_jobs": [
    {
      "source_sequence_name": "Igor26_baby_1_e01",
      "new_sequence_name": "Igor26_baby_1_o01"
    }
  ]
}
```

In normal work, keep one project-specific batch config next to the template, for example `project_sequence_batch_slava_26_1.json`, and re-run the batch from that file instead of editing the template each time.

After a successful batch run, `reports` typically contains:

- the final combined optimized `.prproj`;
- `batch_summary.json`;
- `batch_summary.txt`;
- `batch_transition_recommendations.txt`;
- per-sequence JSON/TXT reports;
- `*_structure.txt`;
- `*_human_profile_report.txt` if personalized reporting was requested;
- `*_transition_recommendations.txt`;
- `temp_projects\*.prproj` intermediate projects.

To build personalized reports automatically inside the batch, enable both:

- `"generate_personalized_report": true`
- `"human_detail_txt": "<LOCAL_PATH>"`

If `generate_personalized_report` stays `false`, the batch works exactly as before and no extra personalized report is created.

## Rebuild Reports After Manual Sequence Changes

If you manually change the optimized sequence after the program finishes, you can rebuild the reports for the current order without running optimization again:

```powershell
python .\main_sequence_reports.py `
  --prproj "<LOCAL_PATH>" `
  --sequence-name "Igor26_baby_1_o01" `
  --optimization-report-json "<LOCAL_PATH>" `
  --output-dir "<LOCAL_PATH>"
```

This command rebuilds:

- `<sequence>_manual_order.json`;
- `<sequence>_manual_order_structure.txt`;
- `<sequence>_manual_order_transition_recommendations.txt`.

Use this when the user manually improved the sequence after automatic optimization and now wants fresh editing, description, and music recommendations for the approved order.

The structure report now separates adult travel/leisure sequences from family portraits more conservatively. Older adults, large groups, or generic portrait/group cues alone should not force the report into a family theme if the sequence is clearly built around travel, rest, and locations.

Repeated pets are also surfaced more explicitly now. If dogs, cats, or other домашние животные appear through multiple clips, `*_structure.txt` should mention that motif in the main theme or the brief description instead of dropping it.

Adult family portrait wording is now gender-neutral by default. The report should not describe a sequence as centered on women or on men unless repeated frame evidence clearly justifies that emphasis.

Short English pet words are matched more carefully now. Words like `capturing` should no longer create a false `cat` motif, while repeated wedding/bride-groom or fishing/fish-fisherman motifs should now surface in `*_structure.txt` when they repeat through the sequence.

When such motifs are present only in part of the sequence, the report should describe them as a noticeable line or accent inside the larger story instead of turning the whole video into only “wedding” or only “fishing”.

Wedding wording is now stricter too: the report should switch to a wedding motif only when explicit bride/groom/wedding cues are present. Generic romantic scenes, couple portraits, or kisses alone should not relabel the whole sequence as a wedding. Travel-dominant family sequences should stay travel-centered.

## Add Human Detail To The Report

Keep the regular `*_structure.txt` as a video-only report.

If you also have a human-written hero description, build one more separate report that overlays:

- what is visible in the video;
- what the human description says about the hero;
- how the music recommendations should be corrected for this person.

Command:

```powershell
python .\main_human_sequence_report.py `
  --optimization-report-json "<LOCAL_PATH>" `
  --human-detail-txt "<LOCAL_PATH>"
```

This creates:

- `01_Maya26_o03_human_profile_report.txt`

The same logic can now run automatically inside `main_project_sequence_batch.py` when the batch config contains:

```json
{
  "generate_personalized_report": true,
  "human_detail_txt": "<LOCAL_PATH>"
}
```

Important rule:

- the main theme, story, and factual structure must stay video-based;
- the human text should adjust hero portrait, wording tone, and music preferences;
- professions, biography facts, diet, education, and other non-visible details should not be turned into direct video facts unless they are visible in the sequence.

## Cleanup Of Old And Temporary Files

Preview cleanup only:

```powershell
python .\main_cleanup_artifacts.py `
  --reports-dir "<LOCAL_PATH>" `
  --older-than-days 7 `
  --include-output-build-dirs `
  --include-test-runtime-items
```

Safe cleanup with archive:

```powershell
python .\main_cleanup_artifacts.py `
  --reports-dir "<LOCAL_PATH>" `
  --older-than-days 7 `
  --include-output-build-dirs `
  --include-test-runtime-items `
  --archive-dir "<LOCAL_PATH>" `
  --execute
```

Notes:

- without `--execute`, the command is a dry run only;
- cleanup reports are written into `output\cleanup_reports`;
- `--include-test-runtime-items` adds top-level `test_runtime` artifacts to the cleanup scan;
- use `--archive-dir` when you want to keep a recoverable copy before deletion.

Recommended one-line workspace cleanup:

```powershell
python .\main_cleanup_artifacts.py --include-output-build-dirs --include-output-files --include-test-runtime-items --archive-dir ".\cleanup_archive\workspace_$(Get-Date -Format yyyyMMdd_HHmmss)" --execute
```

Recommended one-line preview:

```powershell
python .\main_cleanup_artifacts.py --include-output-build-dirs --include-output-files --include-test-runtime-items --archive-dir ".\cleanup_archive\workspace_$(Get-Date -Format yyyyMMdd_HHmmss)"
```

## Final Naming Standard

Use this short naming standard for new projects and new batch configs:

- project in approved manual work: `Igor26_1A_w01.prproj`
- project produced by optimization batch: `Igor26_1A_o01.prproj`
- approved manual sequence: `Igor26_baby_1_e01`
- optimized sequence proposal: `Igor26_baby_1_o01`

Meaning:

- `w` = working project
- `e` = editable and approved manual sequence
- `o` = optimized result from the program
- `01`, `02`, `03` = version number

Recommended cycle:

1. Work manually in `Igor26_1A_w01.prproj`.
2. Keep the approved source sequence as `Igor26_baby_1_e01`.
3. Run optimization and create `Igor26_1A_o01.prproj` with sequence `Igor26_baby_1_o01`.
4. Review and manually refine that optimized sequence.
5. If it becomes the new approved base, save the next manual project as `Igor26_1A_w02.prproj`.
6. Rename the accepted sequence to `Igor26_baby_1_e02`.
7. If another cycle is needed, create `Igor26_1A_o02.prproj` and `Igor26_baby_1_o02`.
8. When the sequence is final, rebuild reports from the final current order and keep them in `reports`.

## Typical Commands

Full cycle:

```bat
run_full_grok_pipeline.bat --upload-timeout 300
```

Background images only:

```bat
run_full_grok_pipeline.bat --skip-video --generate-source-background --upload-timeout 300
```

Full cycle with Grok debug artifacts:

```bat
run_full_grok_pipeline.bat --save-grok-debug-artifacts --upload-timeout 300
```

Grok batch only for already prepared prompt files:

```bat
run_grok_automation_all.bat --upload-timeout 300
```

Premiere sequence optimization batch:

```powershell
python .\main_project_sequence_batch.py --config .\project_sequence_batch_igor_26_1A.json
```

Rebuild reports from current manual order:

```powershell
python .\main_sequence_reports.py --prproj "<project.prproj>" --sequence-name "<sequence>" --optimization-report-json "<report.json>" --output-dir "<reports-dir>"
```

Cleanup preview:

```powershell
python .\main_cleanup_artifacts.py --reports-dir "<reports-dir>" --older-than-days 7 --include-output-build-dirs --include-test-runtime-items
```

One-line workspace safe cleanup:

```powershell
python .\main_cleanup_artifacts.py --include-output-build-dirs --include-output-files --include-test-runtime-items --archive-dir ".\cleanup_archive\workspace_$(Get-Date -Format yyyyMMdd_HHmmss)" --execute
```

One prompt manually:

```bat
run_grok_automation.bat --image .\input\photo.jpg --prompt .\output\photo_20260314_101010_v_prompt_1.txt --upload-timeout 300
```

## Short Operator Recommendations

- Use `login_grok_profile.bat` only when manual Grok login is needed.
- For normal work, run `run_full_grok_pipeline.bat`.
- If you only need backgrounds, use `--skip-video` together with `--generate-source-background`.
- If something goes wrong with Grok result saving, temporarily enable `--save-grok-debug-artifacts`.
- If a stage failed, first check `error\output\<stage_id>\<stage_id>_error.txt`.
- Open final optimized `.prproj` files from `reports`, not from `output`.
- If you changed an optimized sequence manually, rebuild reports with `main_sequence_reports.py`.
- Before deleting old artifacts, run cleanup in dry-run mode first and preferably keep an archive copy.

## Documentation Sync Rule

Whenever workflow, file locations, naming, cleanup rules, or report outputs change, update both guide files together:

- `USER_GUIDE.md`
- `Руководство_пользователя.md`
