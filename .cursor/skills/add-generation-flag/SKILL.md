---
name: add-generation-flag
description: Add a new generation flag to the project end-to-end (config dataclass, validation, serialization, CLI override, JSON config, docs, and tests). Use when the user wants to introduce a new boolean/int/string knob into `GenerationConfig`, asks to wire up a new option through `from_dict`/`override`/`to_dict`, or asks how to expose a setting through `config.json`/`run_full_grok_pipeline*.bat` and the user guide.
---

# Add a Generation Flag

The project keeps generation knobs in `GenerationConfig` (`config.py`). Every new flag must be threaded through all of: the dataclass, validation, `from_dict`, `override`, optional `to_dict`/serialization, the JSON configs, the CLI parser, the docs, and at least one targeted test.

## Decide first

```
- [ ] Name: snake_case, matches existing style (e.g. `generate_*`, `use_*`, `prefer_*`)
- [ ] Type and default: bool/int/str, sensible safe default
- [ ] Scope: does it conflict with framing-mode flags (only one of those can be enabled)?
- [ ] CLI override needed? (true for most production flags)
- [ ] User-facing? If yes, it MUST be documented in USER_GUIDE.md and docs/USER_GUIDE_RU.md
```

## End-to-end checklist

Walk through these files in order:

```
- [ ] config.py: add field to @dataclass GenerationConfig with default value
- [ ] config.py: extend __post_init__ validation if the flag has constraints
- [ ] config.py: extend `from_dict(cls, data)` to read the new key with default fallback
- [ ] config.py: extend `override(self, **kwargs)` to accept the new key
- [ ] config.py: extend `to_dict` / serialization (if used by manifests/reports)
- [ ] config.json + config_BASE.json + relevant profile configs: add the key with default value (only the ones that actually need it - keep deltas minimal)
- [ ] main.py / main_full_pipeline.py / main_full_pipeline_api.py: add argparse flag, forward to `override()` (only the entry points that need it)
- [ ] *.bat launchers: only edit if you need a hardcoded value for that launcher
- [ ] utils/prompt_builder.py / api/grok_*.py / wherever the flag is read: implement the actual behavior
- [ ] USER_GUIDE.md: new subsection under "New Generation Flags" with default, semantics, examples
- [ ] docs/USER_GUIDE_RU.md and docs/USER_GUIDE_EN.md: mirror the doc update
- [ ] test/test_config_cli_defaults.py: verify default + CLI override path
- [ ] test/test_full_pipeline.py or test/test_api_pipeline.py: verify behavior end-to-end if it affects the pipeline
- [ ] Run `python .\main_change_impact.py --change-type generation_flag --changed-file config.py` (or json output) to see the official impact set
- [ ] Run pytest for the touched tests
```

## Templates

### Bool flag

```python
@dataclass
class GenerationConfig:
    ...
    my_new_flag: bool = False  # default OFF
```

```python
# from_dict
my_new_flag=data.get("my_new_flag", default.my_new_flag),
```

```python
# override
"my_new_flag": ("my_new_flag", bool),
```

```python
# argparse (in the entry point)
parser.add_argument("--my-new-flag", dest="my_new_flag", action="store_true")
```

### Int flag with validation

```python
@dataclass
class GenerationConfig:
    ...
    my_new_count: int = 3

    def __post_init__(self) -> None:
        ...
        if not 1 <= self.my_new_count <= 10:
            raise ConfigValidationError("Config key 'my_new_count' must be between 1 and 10.")
```

### Framing-mode flag

Framing-mode flags are mutually exclusive. Extend the `enabled = [...]` list inside `__post_init__` so the validator catches multi-enable mistakes:

```python
enabled = [
    flag for flag, active in (
        ("prefer_face_closeups", self.prefer_face_closeups),
        ...
        ("my_new_framing_flag", self.my_new_framing_flag),
    ) if active
]
```

Also update the error message string so it lists the new flag.

## Documentation pattern

Add a section in `USER_GUIDE.md` -> "New Generation Flags" matching the existing style:

```markdown
### `my_new_flag`

Controls <one-line behavior>. Default: `false`.

When enabled:
- <bullet 1>
- <bullet 2>

CLI override:

\`\`\`bat
run_full_grok_pipeline.bat --my-new-flag --upload-timeout 300
\`\`\`
```

Mirror it in `docs/USER_GUIDE_EN.md` and `docs/USER_GUIDE_RU.md`.

## Change-impact check

Before merging, run the project's own impact analyzer:

```bat
python .\main_change_impact.py --changed-file config.py
python .\main_change_impact.py --change-type generation_flag --changed-file config.py --json
```

It prints the canonical set of files that should be touched for a `generation_flag` change - cross-check against your diff.

## Existing examples to copy from

- Bool: `generate_source_background`, `save_grok_debug_artifacts`, `continue_after_failure`.
- Int with validation: `video_duration_seconds` (6/10), `ai_optimal_then_identity_safe_ai_optimal_percent` (1-99).
- Mutually exclusive framing flags: `prefer_face_closeups`, `use_ai_optimal_framing`, `generate_dual_framing_videos`, `generate_identity_safe_closeup_videos`, `generate_triple_framing_videos`.

## Reference

- `config.py` - `GenerationConfig`, `Settings`, `ConfigValidationError`.
- `docs/CHANGE_IMPACT.md` - canonical impact rules per change type.
- `main_change_impact.py` - impact analyzer entry point.
- `USER_GUIDE.md` -> "New Generation Flags" - doc style guide.
