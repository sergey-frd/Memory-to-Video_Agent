from __future__ import annotations

import argparse
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from api.grok_web import GrokWebSessionRunner
from config import ConfigValidationError, Settings, load_generation_config
from main_grok_web import SUPPORTED_INPUT_SUFFIXES, default_output_video_path, run_generation
from utils.project_delivery import clear_directory_contents, sync_video_file

PROMPT_NAME_RE = re.compile(r"^(?P<image_stem>.+)_\d{8}_\d{6}_v_prompt_(?P<index>\d+)$")


BatchRunner = Callable[[argparse.Namespace], Path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Grok video generation for all v_prompt files in output/.")
    parser.add_argument("--prompt-dir", type=Path, default=None, help="Directory with v_prompt files. Defaults to output/.")
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory with source images. Defaults to input/.")
    parser.add_argument("--config-file", type=Path, default=None, help="Optional generation config JSON.")
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile/grok-web"), help="Persistent Chrome profile for Grok Web.")
    parser.add_argument("--target-url", type=str, default="https://grok.com/imagine", help="Grok Web URL.")
    parser.add_argument("--chrome-exe", type=Path, default=None, help="Optional explicit Chrome executable path.")
    parser.add_argument("--chrome-debug-port", type=int, default=None, help="Optional Chrome remote debugging port for reusing an already opened Grok login window.")
    parser.add_argument("--result-timeout", type=float, default=600.0, help="How long to wait for each generated video, in seconds.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="How long to wait for Grok Web to open, in seconds.")
    parser.add_argument("--upload-timeout", type=float, default=180.0, help="How long to wait for image upload readiness before submit, in seconds.")
    parser.add_argument(
        "--generate-video",
        action="store_true",
        dest="generate_video",
        default=None,
        help="Generate videos in Grok.",
    )
    parser.add_argument(
        "--skip-video",
        action="store_false",
        dest="generate_video",
        help="Skip video generation. If source-background generation is enabled, only background images will be created.",
    )
    parser.add_argument(
        "--generate-source-background",
        action="store_true",
        dest="generate_source_background",
        default=None,
        help="Generate the source-background image in Grok before video generation.",
    )
    parser.add_argument(
        "--skip-source-background",
        action="store_false",
        dest="generate_source_background",
        help="Skip Grok source-background generation even if it is enabled in config.",
    )
    parser.add_argument(
        "--save-grok-debug-artifacts",
        action="store_true",
        dest="save_grok_debug_artifacts",
        default=None,
        help="Keep Grok candidate/debug artifacts in output/ for diagnostics.",
    )
    parser.add_argument(
        "--skip-grok-debug-artifacts",
        action="store_false",
        dest="save_grok_debug_artifacts",
        help="Do not keep Grok candidate/debug artifacts unless enabled in config.",
    )
    parser.add_argument("--no-submit", action="store_true", help="Fill forms without submitting them.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip prompts whose target mp4 already exists.")
    parser.add_argument("--keep-workdirs", action="store_true", help="Keep input/ and output/ contents after a successful batch run.")
    return parser.parse_args()


def list_prompts(prompt_dir: Path) -> list[Path]:
    return sorted(path for path in prompt_dir.glob("*_v_prompt_*.txt") if path.is_file())


def derive_image_stem(prompt_path: Path) -> str:
    match = PROMPT_NAME_RE.match(prompt_path.stem)
    if match:
        return match.group("image_stem")
    if "_v_prompt_" in prompt_path.stem:
        return prompt_path.stem.split("_v_prompt_", 1)[0]
    raise ValueError(f"Could not derive input image stem from prompt file: {prompt_path.name}")


def resolve_image_for_prompt(prompt_path: Path, input_dir: Path) -> Path:
    image_stem = derive_image_stem(prompt_path)
    for suffix in sorted(SUPPORTED_INPUT_SUFFIXES):
        candidate = input_dir / f"{image_stem}{suffix}"
        if candidate.exists():
            return candidate
    matches = sorted(path for path in input_dir.iterdir() if path.is_file() and path.stem == image_stem and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES)
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Could not find source image for prompt '{prompt_path.name}' in {input_dir}")


def run_batch(args: argparse.Namespace, settings: Settings | None = None, runner: BatchRunner | None = None) -> list[Path]:
    settings = settings or Settings()
    settings.ensure_output()
    default_config_path = settings.project_root / "config.json"
    generation_config = load_generation_config(
        args.config_file or (default_config_path if default_config_path.exists() else None)
    ).override(
        generate_video=getattr(args, "generate_video", None),
        generate_source_background=getattr(args, "generate_source_background", None),
        save_grok_debug_artifacts=getattr(args, "save_grok_debug_artifacts", None),
    )

    prompt_dir = args.prompt_dir or settings.output_dir
    input_dir = args.input_dir or settings.input_dir
    prompts = list_prompts(prompt_dir)
    if not prompts:
        raise FileNotFoundError(f"No v_prompt files found in: {prompt_dir}")

    session_runner: GrokWebSessionRunner | None = None
    if runner is None:
        session_runner = GrokWebSessionRunner()

        def resolved_runner(run_args: argparse.Namespace) -> Path:
            return run_generation(run_args, settings=settings, runner=session_runner.run)
    else:
        resolved_runner = runner
    outputs: list[Path] = []
    prepared_background_stages: set[str] = set()

    try:
        for prompt_path in prompts:
            image_path = resolve_image_for_prompt(prompt_path, input_dir)
            output_video = default_output_video_path(prompt_path, settings)
            stage_id = prompt_path.stem.split("_v_prompt_", 1)[0]
            should_generate_background = bool(generation_config.generate_source_background and stage_id not in prepared_background_stages)
            if not generation_config.generate_video and not should_generate_background:
                continue
            if args.skip_existing and output_video.exists():
                sync_video_file(settings, generation_config, output_video)
                print(f"Skipped existing video: {output_video}")
                outputs.append(output_video)
                continue

            run_args = SimpleNamespace(
                image=image_path,
                prompt=prompt_path,
                output_video=output_video,
                    config_file=args.config_file,
                    profile_dir=args.profile_dir,
                    target_url=args.target_url,
                    chrome_exe=args.chrome_exe,
                    chrome_debug_port=getattr(args, "chrome_debug_port", None),
                    result_timeout=args.result_timeout,
                launch_timeout=args.launch_timeout,
                upload_timeout=args.upload_timeout,
                generate_video=generation_config.generate_video,
                generate_source_background=should_generate_background,
                save_grok_debug_artifacts=generation_config.save_grok_debug_artifacts,
                no_submit=args.no_submit,
            )
            result = resolved_runner(run_args)
            outputs.append(result)
            if should_generate_background:
                prepared_background_stages.add(stage_id)
            if args.no_submit:
                print(f"Grok form prepared: {prompt_path.name} -> {result.name}")
            elif result.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                print(f"Grok background image saved: {prompt_path.name} -> {result.name}")
            else:
                print(f"Grok video saved: {prompt_path.name} -> {result.name}")
    finally:
        if session_runner is not None:
            print("Closing Grok for current image...", flush=True)
            session_runner.close()

    if not args.no_submit and not getattr(args, "keep_workdirs", False):
        clear_directory_contents(settings.input_dir)
        clear_directory_contents(settings.output_dir)
        print("Input and output directories cleared after successful batch delivery.")

    return outputs


def main() -> None:
    args = parse_args()
    outputs = run_batch(args)
    print(f"Processed outputs: {len(outputs)}")


if __name__ == "__main__":
    try:
        main()
    except ConfigValidationError as exc:
        raise SystemExit(f"Config validation error: {exc}") from exc
