from __future__ import annotations

import argparse
import ctypes
import sys
from datetime import datetime
from pathlib import Path

from api.openai_video_prompt_composer import (
    synthesize_multiscene_video_prompt_with_openai,
    synthesize_seedance_json_bundle_with_openai,
)
from utils.video_prompt_composer import (
    JERUSALEM_TZ,
    load_video_prompt_request,
    resolve_reference_contexts,
    write_generated_prompt_files,
    write_generated_seedance_prompt_files,
)
from video_prompt_config import VideoPromptComposerConfig, load_video_prompt_composer_config


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
            "Build one multi-scene video-generation prompt in English and one Russian translation, "
            "using scene timing, @image tags, and regeneration_assets descriptions."
        )
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help=(
            "Optional full video-prompt composer config JSON/JSONC file with both scenario data "
            "and Seedance generation settings."
        ),
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        default=None,
        help="Path to the JSON request file. If omitted, JSON is read from stdin.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional OpenAI model override for prompt composition.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to regeneration_assets_dir from the request.",
    )
    parser.add_argument(
        "--seedance-json",
        action="store_true",
        default=None,
        help="Also generate a Seedance 2.0 JSON prompt file alongside the TXT prompts.",
    )
    parser.add_argument(
        "--seedance-json-only",
        action="store_true",
        default=None,
        help="Generate only Seedance JSON files and skip the EN/RU TXT prompt composer.",
    )
    parser.add_argument(
        "--seedance-director-file",
        type=Path,
        default=None,
        help="Path to the Seedance 2.0 director requirements markdown file.",
    )
    return parser.parse_args()


def _read_request_text(request_file: Path | None) -> str:
    if request_file is not None:
        return request_file.read_text(encoding="utf-8")
    request_text = sys.stdin.read()
    if not request_text.strip():
        raise SystemExit("Provide --request-file or pipe JSON request text through stdin.")
    return request_text


def _load_run_config(args: argparse.Namespace) -> VideoPromptComposerConfig:
    if args.config_file is not None and args.request_file is not None:
        raise SystemExit("Use either --config-file or --request-file, not both.")

    if args.config_file is not None:
        config = load_video_prompt_composer_config(args.config_file)
    else:
        request_text = _read_request_text(args.request_file)
        request = load_video_prompt_request(request_text)
        config = VideoPromptComposerConfig(request=request)

    return config.override(
        model=args.model,
        output_dir=args.output_dir,
        seedance_json=args.seedance_json,
        seedance_json_only=args.seedance_json_only,
        seedance_director_file=args.seedance_director_file,
    )


def main() -> None:
    _configure_stdio()
    args = parse_args()
    run_config = _load_run_config(args)
    request = run_config.request
    reference_contexts = resolve_reference_contexts(request)
    output_dir = run_config.output_dir or request.regeneration_assets_dir
    timestamp = datetime.now(JERUSALEM_TZ)
    if run_config.seedance_json:
        director_requirements_text = run_config.seedance_director_file.read_text(encoding="utf-8")
        for scenario_variant in request.scenario_variants:
            seedance_bundle = synthesize_seedance_json_bundle_with_openai(
                request=request,
                reference_contexts=reference_contexts,
                director_requirements_text=director_requirements_text,
                scenario_variant=scenario_variant,
                model=run_config.model,
            )
            seedance_prompt_path, seedance_prompt_ru_path = write_generated_seedance_prompt_files(
                output_dir,
                seedance_bundle,
                timestamp=timestamp,
                variant_suffix=scenario_variant.variant_id,
            )
            print(
                f"Seedance JSON prompt saved for {scenario_variant.label}: {seedance_prompt_path}"
            )
            print(
                f"Seedance RU control JSON prompt saved for {scenario_variant.label}: {seedance_prompt_ru_path}"
            )
    if run_config.seedance_json_only:
        return
    bundle = synthesize_multiscene_video_prompt_with_openai(
        request=request,
        reference_contexts=reference_contexts,
        scenario_variant=request.scenario_variants[0],
        model=run_config.model,
    )
    video_prompt_path, video_prompt_ru_path = write_generated_prompt_files(
        output_dir,
        bundle,
        timestamp=timestamp,
    )
    print(f"English video prompt saved to: {video_prompt_path}")
    print(f"Russian video prompt saved to: {video_prompt_ru_path}")


if __name__ == "__main__":
    main()
