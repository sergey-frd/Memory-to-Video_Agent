from __future__ import annotations

import argparse
import ctypes
import sys
from datetime import datetime
from pathlib import Path

from api.openai_video_prompt_story import synthesize_story_draft_with_openai
from utils.video_prompt_composer import JERUSALEM_TZ
from utils.video_prompt_story import (
    build_empty_story_draft,
    build_story_references,
    default_story_output_paths,
    discover_story_image_candidates,
    load_story_draft_from_html,
    load_story_draft_json,
    resolve_story_reference_contexts,
    select_story_image_candidates,
    story_draft_to_video_prompt_config,
    write_story_draft_json,
    write_story_html,
    write_video_prompt_config,
)
from video_prompt_story_config import VideoPromptStoryConfig, load_video_prompt_story_config


def _configure_stdio() -> None:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            continue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a reviewable multi-scene video story in HTML, then export a "
            "video_prompt_composer JSON config after manual edits."
        )
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        required=True,
        help="Path to video_prompt_story_config JSON/JSONC file.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate a story draft with OpenAI and write HTML/JSON review artifacts.",
    )
    parser.add_argument(
        "--export-config",
        action="store_true",
        help="Export a video_prompt_composer config JSON from a reviewed story HTML/JSON draft.",
    )
    parser.add_argument(
        "--story-html",
        type=Path,
        default=None,
        help="Reviewed story HTML file for --export-config.",
    )
    parser.add_argument(
        "--story-json",
        type=Path,
        default=None,
        help="Reviewed story JSON draft for --export-config. Overrides --story-html when both are set.",
    )
    parser.add_argument(
        "--output-config",
        type=Path,
        default=None,
        help="Optional output path for exported video_prompt_composer config JSON.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional OpenAI model override for story generation.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory for generated story artifacts.",
    )
    return parser.parse_args()


def _load_run_config(args: argparse.Namespace) -> VideoPromptStoryConfig:
    config = load_video_prompt_story_config(args.config_file)
    return config.override(model=args.model, output_dir=args.output_dir)


def _generate_story(config: VideoPromptStoryConfig) -> tuple[Path, Path]:
    candidates = discover_story_image_candidates(config)
    selected_candidates = select_story_image_candidates(config, candidates)
    references = build_story_references(selected_candidates)
    image_paths = tuple(candidate.image_path for candidate in selected_candidates)
    draft = build_empty_story_draft(
        config,
        references=references,
        image_paths=image_paths,
    )
    reference_contexts = resolve_story_reference_contexts(config, references)
    draft = synthesize_story_draft_with_openai(
        config=config,
        draft=draft,
        reference_contexts=reference_contexts,
        model=config.model,
    )

    output_dir = config.effective_output_dir
    html_path, json_path, _composer_config_path = default_story_output_paths(
        output_dir,
        stem=config.story_output_stem,
    )
    write_story_html(html_path, draft)
    write_story_draft_json(json_path, draft)
    return html_path, json_path


def _export_config(
    config: VideoPromptStoryConfig,
    *,
    story_html: Path | None,
    story_json: Path | None,
    output_config: Path | None,
) -> Path:
    if story_json is not None:
        draft = load_story_draft_json(story_json)
    elif story_html is not None:
        draft = load_story_draft_from_html(story_html)
    else:
        raise SystemExit("Provide --story-html or --story-json for --export-config.")

    payload = story_draft_to_video_prompt_config(
        draft,
        model=config.model,
        output_dir=config.effective_output_dir,
        seedance_json=config.seedance_json,
        seedance_json_only=config.seedance_json_only,
        seedance_director_file=config.seedance_director_file,
    )
    if output_config is None:
        stamp = datetime.now(JERUSALEM_TZ).strftime("%Y%m%d_%H%M%S")
        output_config = config.effective_output_dir / f"video_prompt_config_{stamp}.json"
    write_video_prompt_config(output_config, payload)
    return output_config


def main() -> None:
    _configure_stdio()
    args = parse_args()
    if not args.generate and not args.export_config:
        raise SystemExit("Specify --generate and/or --export-config.")

    config = _load_run_config(args)

    if args.generate:
        html_path, json_path = _generate_story(config)
        print(f"Story HTML saved to: {html_path}")
        print(f"Story JSON draft saved to: {json_path}")
        print(
            "Review the HTML, edit the story, click 'Обновить черновик', "
            "then run with --export-config."
        )

    if args.export_config:
        output_config = _export_config(
            config,
            story_html=args.story_html,
            story_json=args.story_json,
            output_config=args.output_config,
        )
        print(f"Video prompt composer config saved to: {output_config}")
        print(
            "Next step:\n"
            f"  .\\.venv\\Scripts\\python.exe -u .\\main_video_prompt_composer.py "
            f"--config-file {output_config}"
        )


if __name__ == "__main__":
    main()
