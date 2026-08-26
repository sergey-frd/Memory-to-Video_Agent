# img-style-ag_1

Dev workspace for the Memory-to-Video_Agent source project.

## Git remotes

| Remote | Repository | Purpose |
| --- | --- | --- |
| `origin` | https://github.com/sergey-frd/img-style-ag_1 | Private dev history (full working tree) |
| `publication` | https://github.com/sergey-frd/Memory-to-Video_Agent | Public publication bundle (`source/` mirror + docs) |

Do **not** push this workspace root to `publication`. Use `main_project_publication_push.py` instead.

## Current version

- Workspace `VERSION`: **`2026.08.26.02`** ([`VERSION`](VERSION))
- Latest intended publication tag: **`v2026.08.26.02`**

## Published on GitHub (Internet)

Public repository:

- https://github.com/sergey-frd/Memory-to-Video_Agent

This release wave (**2026.08.26.02**) adds reusable JSON-driven Premiere sequence editing and Motion:

- `premiere_sequence_motion_animation` for relative intrinsic Scale/Position keyframes on a protected sequence copy
- `premiere_sequence_insert_from_sequence_and_motion_animation` for frame-exact video-only insertion from another in-project sequence followed by static-only Motion
- Explicit JSON decisions through `resolved_source_range_frames` and `resolved_destination_frame`
- Non-ripple output audio removal, silent review export, dry-run plans, structural QA, and milestone sequence validation
- Reusable templates, complete RU examples, synchronized RU/EN guides, and 74 passing Premiere regression tests
- Entry point: `run_premiere_sequence_motion.bat`

Also carries earlier waves already on the public repo (import / keep-apply / import-and-keep, expanded portrait banks, hero-aware Sequence Trim Review).

## Operator docs

- [`docs/README.md`](docs/README.md) — documentation index
- [`docs/USER_GUIDE_EN.md`](docs/USER_GUIDE_EN.md) / [`docs/USER_GUIDE_RU.md`](docs/USER_GUIDE_RU.md)
- [`docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md`](docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md) — parameter → program → batch → result
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)
- [`docs/PUBLISHING.md`](docs/PUBLISHING.md)
- [`docs/BATCH_RUN_HISTORY.md`](docs/BATCH_RUN_HISTORY.md)
- [`docs/PREMIERE_JSON_EDIT_AND_MOTION_RU.md`](docs/PREMIERE_JSON_EDIT_AND_MOTION_RU.md)
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
