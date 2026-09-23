# Portrait configuration examples

Copy templates to a new local job folder. Replace paths, expected_count, real catalog IDs, context and narrative. The placeholders are not runnable sample media. Choose a supported model explicitly for the authorized task.

From the public repository source directory:

```powershell
python -B tools/run_portrait_draft.py --config <your-full.json> --check
python -B tools/run_short_portrait.py --config <your-short.json> --check
python -B tools/render_structure_draft.py --config <your-render.json> --dry-run
```

The SHORT launcher only selects its parent FULL IDs. Independent alternative edits require a separately prepared edit plan. Removing check/dry-run performs work and may make paid API calls for FULL/SHORT. Never run old personal JSX jobs to initialize a new hero.

Paths are resolved by each script; absolute paths are recommended for a local job. These files configure different stages; they are not interchangeable. See ../../docs/PORTRAIT_WORKFLOW_CHOICES_RU.md and ../../docs/PORTRAIT_MODES_RU.md.
