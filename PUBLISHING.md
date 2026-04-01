# Publishing Workflow

This repository is intended to contain only the managed publication bundle exported from the source project.
The current bundle includes a limited public code snapshot: core entry/config files plus selected Python sources from `api/` and `utils/`.

## Safe Update Flow

1. Refresh the bundle into this local clone.
2. Stage only the managed files from `data/publication_manifest.json`.
3. Review `git diff --staged`.
4. Commit and push only after the staged diff looks correct.

## Commands

```powershell
python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --stage
python .\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --commit-message "Update project publication" --push
```

## Safety Rules

- Do not push the working project root directly.
- Do not copy `.env`, `input`, `output`, browser profiles, or temporary directories into this repository.
- Publish only the managed code snapshot in `code/`: root `main/config` files plus selected Python sources from `api/` and `utils/`.
- The publication sync blocks secret-like content and sanitizes local absolute paths.
- `.gitignore` in this repository is generated to keep the repo limited to the managed publication files.
