# img-style-ag_1

Dev workspace for the Memory-to-Video_Agent source project.

## Git remotes

| Remote | Repository | Purpose |
| --- | --- | --- |
| `origin` | https://github.com/sergey-frd/img-style-ag_1 | Private dev history (full working tree) |
| `publication` | https://github.com/sergey-frd/Memory-to-Video_Agent | Public publication bundle (`source/` mirror + docs) |

Do **not** push this workspace root to `publication`. Use `main_project_publication_push.py` instead.

## Current version

- Workspace `VERSION`: **`2026.07.22.01`** ([`VERSION`](VERSION))
- Latest intended publication tag: **`v2026.07.22.01`**

## Published on GitHub (Internet)

Public repository:

- https://github.com/sergey-frd/Memory-to-Video_Agent

This release wave (**2026.07.25.01**) adds hero-aware Sequence Trim Review and consolidated documentation:

- Heuristic + semantic KEEP/DROP segmentation inside each Premiere clip
- Compact keep for stills (~1.5–3s) and short video islands (~2–8s)
- Review `.prproj` with KEEP on V1 and DROP on V2
- Hero Definition plus HIGH/MEDIUM/REVIEW/DROP matching and report replay
- Canonical documentation consolidated under `docs/`
- Entry points: `main_sequence_trim_review.py`, `run_sequence_trim_review.bat`, skill `sequence-trim-review`

Also carries earlier waves already on the public repo (video story dynamic rules, portrait batch tooling, publication workflow).

## Operator docs

- [`docs/README.md`](docs/README.md) — documentation index
- [`docs/USER_GUIDE_EN.md`](docs/USER_GUIDE_EN.md) / [`docs/USER_GUIDE_RU.md`](docs/USER_GUIDE_RU.md)
- [`docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md`](docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md) — parameter → program → batch → result
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)
- [`docs/PUBLISHING.md`](docs/PUBLISHING.md)
- [`docs/BATCH_RUN_HISTORY.md`](docs/BATCH_RUN_HISTORY.md)
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
