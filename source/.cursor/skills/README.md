# Project Skills

Project-scope Cursor Agent Skills for the Memory-to-Video_Agent workspace. Each subdirectory contains a `SKILL.md` that teaches the agent how to perform a specific recurring task in this project.

Skills here load automatically when the workspace is open. They are derived from the canonical project documentation in `docs/`, the root `README.md` / `CHANGELOG.md`, and the source code under `api/`, `utils/`, `main_*.py`.

## Available skills

| Skill | When the agent should pick it up |
| --- | --- |
| [`grok-video-pipeline`](grok-video-pipeline/SKILL.md) | User wants to generate videos through Grok (web automation `run_full_grok_pipeline.bat` OR direct xAI API `run_full_grok_pipeline_api.bat`). Covers `XAI_API_KEY` setup and the `video_duration_seconds` button fix. |
| [`chatgpt-portrait-batch`](chatgpt-portrait-batch/SKILL.md) | User wants to generate stylized portraits or pair portraits through ChatGPT / Gemini / Grok desktop or web, OpenAI API, or the local stylizer. |
| [`cleanup-temp-files`](cleanup-temp-files/SKILL.md) | User wants to safely clean `test_runtime/`, `__pycache__`, `pytest-cache-files-*`, or other temp artifacts via `main_cleanup_artifacts.py`. |
| [`add-generation-flag`](add-generation-flag/SKILL.md) | User wants to add a new flag to `GenerationConfig` end-to-end (dataclass, validation, `from_dict`, `override`, JSON configs, CLI, docs, tests). |
| [`sequence-optimization-batch`](sequence-optimization-batch/SKILL.md) | User wants to run the Premiere sequence optimization batch (`main_project_sequence_batch.py`) or rebuild reports after manual edits (`main_sequence_reports.py`). |
| [`sequence-trim-review`](sequence-trim-review/SKILL.md) | User wants to shorten raw Premiere footage into compact KEEP/DROP segments (heuristic + semantic) via `main_sequence_trim_review.py`. |
| [`sequence-media-copy`](sequence-media-copy/SKILL.md) | User wants to extract and copy all images (and optionally videos) used by a Premiere `.prproj` sequence into destination folders via `main_copy_sequence_media_batch.py` / `copy_sequence_media_*.json`. |
| [`video-prompt-story`](video-prompt-story/SKILL.md) | User wants to preview/edit a multi-scene story in HTML from restored photos + `regeneration_assets`, export `video_prompt_config_*.json`, then run Seedance composer. |
| [`project-publication`](project-publication/SKILL.md) | User wants to stage, commit, tag, or push the publication snapshot to `Memory-to-Video_Agent.git` via `main_project_publication.py` / `main_project_publication_push.py`. |

## How skills are structured

Each `SKILL.md` follows the format documented in `~/.cursor/skills-cursor/create-skill/SKILL.md`:

- YAML frontmatter with `name` (slug, max 64 chars) and `description` (third-person, specific, includes trigger terms).
- Concise body, kept under ~500 lines.
- Tables, checklists, and copyable command blocks for the most common workflows.
- A short "Reference" section pointing at the actual source files in this project.

## Adding a new skill

1. Pick a focused, recurring task that comes up repeatedly in this project.
2. Create `.cursor/skills/<skill-name>/SKILL.md`.
3. Use lowercase + hyphens for `name`; write the description so it lists both WHAT and WHEN.
4. Run the project skill `create-skill` (in `~/.cursor/skills-cursor/create-skill/SKILL.md`) for the full authoring checklist.
5. Update this `README.md` table.
