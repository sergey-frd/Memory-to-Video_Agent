# img-style-ag_1

Dev workspace for the Memory-to-Video_Agent source project.

## Git remotes

| Remote | Repository | Purpose |
| --- | --- | --- |
| `origin` | https://github.com/sergey-frd/img-style-ag_1 | Private dev history (full working tree) |
| `publication` | https://github.com/sergey-frd/Memory-to-Video_Agent | Public publication bundle (`source/` mirror + docs) |

Do **not** push this workspace root to `publication`. Use `main_project_publication_push.py` instead.

## Current version

- Workspace `VERSION`: see [`VERSION`](VERSION)
- Latest publication on GitHub: **`2026.06.10.02`** (`v2026.06.10.02`)

## Published on GitHub (Internet)

Everything through **2026-06-10** is synchronized to the public repository:

- https://github.com/sergey-frd/Memory-to-Video_Agent
- Tag: https://github.com/sergey-frd/Memory-to-Video_Agent/releases/tag/v2026.06.10.02

Included in this publication wave:

- Multi-scene video story HTML preview workflow (`main_video_prompt_story.py`, skill, docs)
- ChatGPT portrait batch result-capture fixes and configurable `result_timeout`
- Project configs `config_‭AlxKrvz.json`, `config_‭IgorSv.json`
- Cursor rule `.cursor/rules/always-russian.mdc`

Dev-only history (private `origin`) additionally stores local batch configs and the full commit trail between publication snapshots.

## Operator docs

- [`USER_GUIDE.md`](USER_GUIDE.md) / [`Руководство_пользователя.md`](Руководство_пользователя.md)
- [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md)
- [`PUBLISHING.md`](PUBLISHING.md)
- [`BATCH_RUN_HISTORY.md`](BATCH_RUN_HISTORY.md)

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
