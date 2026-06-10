---
name: project-publication
description: Stage, commit, tag, and push the public project snapshot to the `Memory-to-Video_Agent` GitHub repository using `main_project_publication.py` and `main_project_publication_push.py`. Use when the user wants to publish a new project snapshot, bump the publication version, sync `source/` mirror and `docs/` bundle into the publication repo, or run `run_project_publication_stage.bat` / `run_project_publication_push.bat`.
---

# Project Publication

The publication pipeline copies a curated bundle (source mirror + docs + machine-readable snapshot) from this project into a separate clone of the `Memory-to-Video_Agent` GitHub repo, then stages/commits/tags/pushes it.

Default remote: `https://github.com/sergey-frd/Memory-to-Video_Agent.git`.

## Two-step model

| Step | Script / launcher | Purpose |
| --- | --- | --- |
| Stage | `main_project_publication.py` / `run_project_publication_stage.bat` | Refresh the publication bundle inside the publication repo clone. No git operations beyond optional staging. |
| Push | `main_project_publication_push.py` / `run_project_publication_push.bat` | Refresh + optionally stage, commit, tag, and push. |

The push tool will refuse to operate against the wrong remote (`--expected-remote-url` guards this) and tracks managed-vs-stale files so deletions are explicit.

## Prerequisites

```
- [ ] A local clone of Memory-to-Video_Agent.git on disk, on the `main` branch, clean working tree.
- [ ] `VERSION` in the source project is bumped to the new publication version (e.g. `2026.05.25.01`).
- [ ] Source-project docs (`README.md`, `USER_GUIDE.md`, `PROJECT_STRUCTURE.md`, `BATCH_RUN_HISTORY.md`, `docs/*`) reflect the changes you're about to publish.
- [ ] No uncommitted changes inside the publication repo clone (the tool stages only managed files, but it's still cleaner to start clean).
- [ ] You have push rights on the GitHub repo if you intend to use --push.
```

## Stage-only (dry friendly)

Refresh the bundle inside the publication repo and stop:

```bat
.\run_project_publication_stage.bat --source-root . --dry-run
```

Or direct call:

```powershell
python .\main_project_publication.py --target-dir "<LOCAL_PATH>"
```

Inspect the diff in the publication repo before pushing:

```powershell
cd "<LOCAL_PATH>"
git status
git diff --stat
```

## Full publish: stage + commit + tag + push

```bat
.\run_project_publication_push.bat --source-root .
```

Or with explicit args:

```powershell
python .\main_project_publication_push.py `
  --repo-dir "<LOCAL_PATH>" `
  --source-root . `
  --expected-remote-url "https://github.com/sergey-frd/Memory-to-Video_Agent.git" `
  --remote-name origin `
  --stage `
  --commit-message "Publish 2026.05.25.01: direct xAI API pipeline + duration fix" `
  --push
```

Notes:
- `--commit-message` implies `--stage`. Without it, the tool only refreshes and stages without committing.
- `--push` requires a commit message when there are staged changes.
- The publication tool reads `VERSION` and creates an annotated tag `v<VERSION>` (e.g. `v2026.05.25.01`).
- Pass `--json` to get a machine-readable result (`PublicationPushResult`) instead of the human-readable summary.

## What gets published

From the source root, the tool selects:

- All entry-point `main_*.py` and `main.py`/`main1.py`.
- All `run_*.bat` and `login_*.bat`.
- `api/`, `services/`, `models/`, `styles/`, `utils/`, `tests/`, `test/`.
- All `config*.json`, `chatgpt_*_config.json`, `project_sequence_batch_*.json`, `video_prompt_*.json/.py`.
- `requirements.txt`, `pytest.ini`, `setup_project.ps1`, `deploy_and_run.ps1`.
- `project_structure_registry.json`.
- The top-level `README.md`, `USER_GUIDE.md`, `PROJECT_STRUCTURE.md`, `BATCH_RUN_HISTORY.md`, `PUBLISHING.md`.
- `docs/` (PROJECT_OVERVIEW, PROJECT_STRUCTURE, USER_GUIDE_EN, USER_GUIDE_RU, CHANGE_IMPACT).
- `data/project_snapshot.json`, `data/publication_manifest.json`.

Stale files inside the publication repo that are NOT in the managed set get removed - that's how the publication keeps in sync.

## Output summary (push tool prints)

```
Publication repo prepared: <path>
Remote: <url>
Branch: <branch>
Version: <VERSION>
Git tag: v<VERSION>
Managed files: <N>
Removed stale files: <N>
Staged files: <N>
Tagged: <bool>
Committed: <bool>
Pushed: <bool>
```

## Safety rules built into the tool

- Refuses if remote URL does not match `--expected-remote-url`.
- Refuses if the branch is not what's expected for publication.
- Refuses to push without an explicit commit message when staged changes exist.
- Tag creation skips silently if `v<VERSION>` already exists locally; verify with `git tag -l v<VERSION>`.

## Reference

- `main_project_publication.py` - stage/refresh entry point.
- `main_project_publication_push.py` - full stage/commit/tag/push entry point.
- `run_project_publication_stage.bat`, `run_project_publication_push.bat` - launchers.
- `PUBLISHING.md` - prose checklist for the operator.
- `VERSION` - drives the publication version and tag name.
