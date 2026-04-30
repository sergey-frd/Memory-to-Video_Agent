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
