---
name: cleanup-temp-files
description: Safely discover and remove stale temporary artifacts from the workspace using `main_cleanup_artifacts.py`. Use when the user asks to clean temporary files, clear `test_runtime/`, remove `__pycache__`, prune `pytest-cache-files-*`, reset `output/` build dirs, archive old project work, or asks for a dry-run report before deletion.
---

# Cleanup Temporary Files

`main_cleanup_artifacts.py` is the single source of truth for safe deletion in this project. It defaults to **dry-run** and writes a timestamped JSON + text report under `output/cleanup_reports/`.

## Golden rule

Never delete with raw `Remove-Item` / `rm -rf` first. Always run the cleanup tool in dry-run, inspect the report, then re-run with `--execute`.

## Categories the tool handles

| Category | What it catches | Default included? |
| --- | --- | --- |
| `cache` | `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | Yes |
| `test-runtime-item` | Items under `test_runtime/` | Only with `--include-test-runtime-items` |
| `temp_projects` | `reports/.../temp_projects/` | Yes |
| `output-build-dir` | Legacy build dirs inside `output/` | Only with `--include-output-build-dirs` |
| `output-file` | Top-level generated artifacts inside `output/` | Only with `--include-output-files` |

`.venv/` and any sibling `site-packages/__pycache__` are NEVER touched - the tool stays inside the project tree.

## Recommended workflow

```
- [ ] Dry-run with the categories you intend to delete; read the report
- [ ] Confirm the list (categories + counts) matches expectations
- [ ] Re-run with --execute; verify "Deleted paths" count matches the dry-run
- [ ] Optional: pass --archive-dir to copy candidates before deletion (full safety net)
```

## Common commands

Quick sweep of caches + leftover `test_runtime/` items:

```bat
.\.venv\Scripts\python.exe .\main_cleanup_artifacts.py --include-test-runtime-items
```

Same, but actually delete:

```bat
.\.venv\Scripts\python.exe .\main_cleanup_artifacts.py --include-test-runtime-items --execute
```

Aggressive cleanup including `output/` artifacts (use with care):

```bat
.\.venv\Scripts\python.exe .\main_cleanup_artifacts.py --include-test-runtime-items --include-output-build-dirs --include-output-files --execute
```

Only items older than 7 days, archive before deletion:

```bat
.\.venv\Scripts\python.exe .\main_cleanup_artifacts.py --include-test-runtime-items --older-than-days 7 --archive-dir .\cleanup_archive --execute
```

Scan an extra reports directory (e.g. a user project's `reports`):

```bat
.\.venv\Scripts\python.exe .\main_cleanup_artifacts.py --reports-dir "E:\Git\P_h_o_t_o\Dv_Yakov_1\...\2026\reports" --execute
```

## What to clean manually (outside the tool)

`pytest-cache-files-*` directories live at the workspace root and are gitignored, but the tool does not pick them up. Remove them by hand when they accumulate:

```powershell
Get-ChildItem -Directory -Filter "pytest-cache-files-*" | Remove-Item -Recurse -Force
```

`.browser-profile/` is a runtime cache and is gitignored. Delete only when Chrome is fully closed; the user will need to re-run `login_*_profile.bat` after.

## Reading the report

`output/cleanup_reports/cleanup_<ts>.txt` lists every candidate with `[<category>] <path> | age <days> | <reason>`. The companion `.json` has the same data machine-readable. Group categories with:

```powershell
Get-Content .\output\cleanup_reports\cleanup_<ts>.txt | Select-String -Pattern "^- \[(\w[\w-]*)\]" | ForEach-Object { ($_ -split '\[')[1].Split(']')[0] } | Group-Object | Sort-Object Count -Descending
```

## Reference

- `main_cleanup_artifacts.py` - CLI.
- `utils/artifact_cleanup.py` - discovery + execution logic.
- `.gitignore` - already excludes `test_runtime/`, `output/`, `pytest-cache-files-*/`, `__pycache__/`, etc.
