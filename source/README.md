# img-style-ag_1

Dev workspace for the Memory-to-Video_Agent source project.

## Git remotes

| Remote | Repository | Purpose |
| --- | --- | --- |
| `origin` | https://github.com/sergey-frd/img-style-ag_1 | Private dev history (full working tree) |
| `publication` | https://github.com/sergey-frd/Memory-to-Video_Agent | Public publication bundle (`source/` mirror + docs) |

Do **not** push this workspace root to `publication`. Use `main_project_publication_push.py` instead.

## Current version

- Workspace `VERSION`: **`2026.08.31.01`** ([`VERSION`](VERSION))
- Release tag: **`v2026.08.31.01`**

## Install this release on a laptop

Use **img-style-ag_1** for the same private development code as the desktop.
The public **Memory-to-Video_Agent** repository is an alternative distribution,
not a second dependency. Choose one repository and the same release tag.

```powershell
git clone https://github.com/sergey-frd/img-style-ag_1.git
cd img-style-ag_1
git switch --detach v2026.08.31.01
.\install_project.bat
.\run_verify_installation.bat --require-tag
```

Requires Windows AMD64 and Python 3.14.2. Setup installs the desktop dependency
lock and bundled FFmpeg fallback. Existing local secrets/configs are preserved.
Premiere, plugins, media, keys and browser logins are transferred separately.
See [the Russian laptop checklist](docs/INSTALL_ON_NEW_COMPUTER_RU.md).

## Release 2026.08.31.01

- Integrated ART TASK_031–034 code and native TASK_032 JSX executors.
- Explicit local JSON profiles, guarded ART launcher, import/help safety and overwrite protection.
- Locked desktop dependencies, fail-fast setup and release verification batch files.
- Public bundle excludes local configs/TASK assets and includes safe env templates and JSX.
- [ART contracts and QA limitations](docs/PREMIERE_ART_TASKS_031_034_RU.md).

## Published on GitHub (Internet)

Public repository:

- https://github.com/sergey-frd/Memory-to-Video_Agent

The previous release (**2026.08.26.02**) added reusable JSON-driven Premiere sequence editing and Motion:

- `premiere_sequence_motion_animation` for relative intrinsic Scale/Position keyframes on a protected sequence copy
- `premiere_sequence_insert_from_sequence_and_motion_animation` for frame-exact video-only insertion from another in-project sequence followed by static-only Motion
- Explicit JSON decisions through `resolved_source_range_frames` and `resolved_destination_frame`
- Non-ripple output audio removal, silent review export, dry-run plans, structural QA, and milestone sequence validation
- Reusable templates, complete RU examples, synchronized RU/EN guides, and 74 passing Premiere regression tests
- Entry point: `run_premiere_sequence_motion.bat`

Also carries earlier waves already on the public repo (import / keep-apply / import-and-keep, expanded portrait banks, hero-aware Sequence Trim Review).

## Release 2026.08.30.01

- Task-specific Premiere timeline assembly, delete/insert/replace, SHORT core/expansion, dual refinement, adaptive still Motion, and Lumetri color/light finishing.
- Alla material-bank/skeleton assembly and client-motion scripts with fixed project paths.
- API pipeline resolves an explicit `--image` from its actual parent folder.
- Updated [task workflow reference](docs/PREMIERE_TASK_WORKFLOWS_RU.md), RU/EN guides, [JSON examples](examples/premiere/), and safe-by-default [script examples](examples/scripts/).

The new task executors have fixed contracts and can update the input project after backup. They are not additional generic Motion modes. The release includes synchronized documentation/examples, a cleaned workspace and a guarded public source bundle. Personal TASK assets stay in the private dev repository.

## Operator docs

- [`docs/README.md`](docs/README.md) — documentation index
- [`docs/USER_GUIDE_EN.md`](docs/USER_GUIDE_EN.md) / [`docs/USER_GUIDE_RU.md`](docs/USER_GUIDE_RU.md)
- [`docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md`](docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md) — parameter → program → batch → result
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)
- [`docs/PUBLISHING.md`](docs/PUBLISHING.md)
- [`docs/BATCH_RUN_HISTORY.md`](docs/BATCH_RUN_HISTORY.md)
- [`docs/PREMIERE_JSON_EDIT_AND_MOTION_RU.md`](docs/PREMIERE_JSON_EDIT_AND_MOTION_RU.md)
- [`docs/PREMIERE_TASK_WORKFLOWS_RU.md`](docs/PREMIERE_TASK_WORKFLOWS_RU.md) — TASK_019–030 and Alla, CLI contracts, examples, backups and QA
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
