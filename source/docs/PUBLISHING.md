# Publishing Workflow

This repository is intended to contain only the managed publication bundle exported from the source project.
The current bundle includes a full safe source mirror under `source/`, excluding secrets and runtime-only folders.
Each successful guarded publication commit can also receive a matching Git tag derived from the generated `VERSION` file.

## Safe Update Flow

1. Refresh the bundle into this local clone.
2. Stage only the managed files from `data/publication_manifest.json`.
3. Review `git diff --staged` and the root `VERSION` file.
4. Commit and push only after the staged diff looks correct.
5. Keep the generated Git tag aligned with the publication version.

## Commands

```powershell
python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --stage
python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --commit-message "Update project publication" --push
```

## Safety Rules

- Do not push the working project root directly.
- Do not copy `.env`, `input`, `output`, browser profiles, or temporary directories into this repository.
- Publish only the managed `source/` mirror plus generated docs/data; runtime folders and secret files stay excluded.
- The publication sync blocks secret-like content and sanitizes local absolute paths.
- `VERSION`, `README.md`, and `data/project_snapshot.json` should agree on the current publication version.
- `.gitignore` in this repository is generated to keep the repo limited to the managed publication files.

## Dev workspace vs publication remote

| Remote | URL | Use |
| --- | --- | --- |
| `origin` | https://github.com/sergey-frd/img-style-ag_1 | Push dev commits (`git push origin main`) |
| `publication` | https://github.com/sergey-frd/Memory-to-Video_Agent | Read-only reference; bundle is pushed by `main_project_publication_push.py` into the local clone at `<LOCAL_PATH>` |

The guarded publisher commits and pushes `origin` from the separate publication clone. Never push the development workspace root to the `publication` remote; that would expose the wrong tree.

## Release 2026.08.30.01

Includes the task-specific Premiere executors, external-image API fix,
updated RU/EN documentation and JSON/PowerShell examples.

Temporary caches and test fixtures are removed before publishing. Actual
TASK_026/027 analysis materials are preserved under `TASK_ARCHIVE/` in the
private development repository, along with TASK_028–030 results. The public
bundle excludes all `TASK_*` directories, `tmp*`, runtime input/output folders,
secrets and the old nested `source/` snapshot. Run public scripts from the
publication repository's `source/` directory; task-specific inputs may require
private local assets and are not supplied as public media.

Root publication docs link examples under `source/examples/`. The bundle also
includes JSONC configs and the documentation renderer. Use the guarded flow
above; review the managed-file list before commit/push.

## GitHub sync status (2026-08-26)

Publication **`2026.08.26.02`** targets the Internet:

- Repository: https://github.com/sergey-frd/Memory-to-Video_Agent
- Dev remote: https://github.com/sergey-frd/img-style-ag_1
- Tag: `v2026.08.26.02`

Headline feature: reusable JSON-driven Premiere intrinsic Motion and frame-exact in-project sequence-range insertion, with dry-run/QA, non-ripple silent output, templates, and complete examples.

Do **not** read version from nested `source/data/project_snapshot.json`; that stale copy is no longer published. Older tags are historical snapshots only; `main` and the latest tag reflect the current bundle.

## Release 2026.08.31.01 and installation identity

Use the private development repository and the same `v2026.08.31.01` tag to
reproduce desktop code on a personal laptop. The public bundle is a separate
sanitized distribution; its commit ID differs. It is not a Git submodule.
See [the installation checklist](INSTALL_ON_NEW_COMPUTER_RU.md).

Publish only source files, templates and docs. JSX and the empty `.env.template`
are included; `*.local.json`, actual `.env`, media and TASK runtime folders are
excluded. ART jobs are not rerun during a code release. Preserve local reports
and Premiere outputs. Commit/tag private code, publish the guarded public
bundle, then verify both remote tags.
