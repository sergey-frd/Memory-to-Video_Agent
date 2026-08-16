# img-style-ag_1

Dev workspace for the Memory-to-Video_Agent source project.

## Git remotes

| Remote | Repository | Purpose |
| --- | --- | --- |
| `origin` | https://github.com/sergey-frd/img-style-ag_1 | Private dev history (full working tree) |
| `publication` | https://github.com/sergey-frd/Memory-to-Video_Agent | Public publication bundle (`source/` mirror + docs) |

Do **not** push this workspace root to `publication`. Use `main_project_publication_push.py` instead.

## Current version

- Workspace `VERSION`: **`2026.08.16.01`** ([`VERSION`](VERSION))
- Latest intended publication tag: **`v2026.08.16.01`**

## Published on GitHub (Internet)

Public repository:

- https://github.com/sergey-frd/Memory-to-Video_Agent

This release wave (**2026.08.16.01**) adds Premiere import / keep-apply and expanded portrait style banks:

- Manual KEEP JSON apply (`apply_keep_ranges`) with ripple-compact and linked audio trim
- Media import into a sequence (`import_media`) with per-file MasterClip / streams
- Import then keep in one pass (`import_and_keep`)
- Music-first recommendation launcher `run_sequence_music_recommendation.bat`
- Portrait banks: `ILYA_REPIN`, selected artists, Russian artists, and the name/slug table in [`docs/portrait_styles_tables.md`](docs/portrait_styles_tables.md)
- Entry points: `run_sequence_keep_apply.bat`, `run_sequence_media_import.bat`, `run_sequence_import_and_keep.bat`, `run_sequence_music_recommendation.bat`, skill `sequence-trim-review`

Also carries earlier waves already on the public repo (hero-aware Sequence Trim Review, documentation consolidation, video story rules).

## Operator docs

- [`docs/README.md`](docs/README.md) — documentation index
- [`docs/USER_GUIDE_EN.md`](docs/USER_GUIDE_EN.md) / [`docs/USER_GUIDE_RU.md`](docs/USER_GUIDE_RU.md)
- [`docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md`](docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md) — parameter → program → batch → result
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)
- [`docs/PUBLISHING.md`](docs/PUBLISHING.md)
- [`docs/BATCH_RUN_HISTORY.md`](docs/BATCH_RUN_HISTORY.md)
- [`docs/portrait_styles_tables.md`](docs/portrait_styles_tables.md) — portrait `name` / `slug` table
- [`CHANGELOG.md`](CHANGELOG.md) — what's new

## Publish command

```powershell
python .\main_project_publication_push.py `
  --repo-dir "<LOCAL_PATH>" `
  --source-root . `
  --commit-message "Publish <VERSION>: <summary>" `
  --push
```

Or:

```bat
.\run_project_publication_push.bat --source-root .
```
