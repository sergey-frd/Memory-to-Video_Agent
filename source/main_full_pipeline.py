from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from types import SimpleNamespace

from config import ConfigValidationError, GenerationConfig, Settings, load_generation_config
from api.grok_web import GrokWebSessionRunner
from main import _configure_stdio, _run_generation, _write_music_prompt
from main_grok_web import run_generation
from main_desktop_pipeline import (
    resolve_input_images,
    stage_id_for_image,
    stage_identifier,
    write_pipeline_manifest,
)
from main_grok_batch import run_batch
from utils.project_delivery import (
    clear_directory_contents,
    move_files_to_directory,
    move_input_files_to_error,
    move_output_stage_to_error,
    sync_stage_non_video_assets,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Full pipeline: generate prompts for one input image, run Grok for that image, then continue with the next image."
    )
    parser.add_argument("--image", "-i", type=Path, required=False, help="Optional single source image path.")
    parser.add_argument("--stage-id", "-s", type=str, default=None, help="Optional fixed stage identifier.")
    parser.add_argument("--config-file", type=Path, default=None, help="Optional generation config JSON.")
    parser.add_argument("--model", "-m", type=str, default="dall-e-2", help="OpenAI image edit model.")
    parser.add_argument("--scene-model", type=str, default=None, help="Optional OpenAI vision model for scene analysis.")
    parser.add_argument("--prompt-model", type=str, default="gpt-4.1-mini", help="OpenAI text model for prompt synthesis.")
    parser.add_argument("--motion-model", type=str, default=None, help="OpenAI text model for AI motion selection.")
    parser.add_argument(
        "--generate-video",
        action="store_true",
        dest="generate_video",
        default=None,
        help="Enable video generation for each input image.",
    )
    parser.add_argument(
        "--skip-video",
        action="store_false",
        dest="generate_video",
        help="Disable video generation. If source-background generation is enabled, only background images will be created.",
    )
    parser.add_argument("--generate-styled-images", action="store_true", help="Generate styled images during the prompt stage.")
    parser.add_argument("--generate-final-frames", action="store_true", dest="generate_final_frames", default=None, help="Generate final frames through the image API.")
    parser.add_argument("--skip-final-frames", action="store_false", dest="generate_final_frames", help="Skip final frame generation.")
    parser.add_argument("--generate-source-background", action="store_true", dest="generate_source_background", default=None, help="Create Grok background prompts/images.")
    parser.add_argument("--skip-source-background", action="store_false", dest="generate_source_background", help="Disable background prompt/image generation.")
    parser.add_argument("--save-grok-debug-artifacts", action="store_true", dest="save_grok_debug_artifacts", default=None, help="Keep Grok candidate/debug artifacts in output/ for diagnostics.")
    parser.add_argument("--skip-grok-debug-artifacts", action="store_false", dest="save_grok_debug_artifacts", help="Do not keep Grok candidate/debug artifacts unless enabled in config.")
    parser.add_argument("--generate-music", action="store_true", dest="generate_music", default=None, help="Generate music prompts per stage.")
    parser.add_argument("--skip-music", action="store_false", dest="generate_music", help="Skip music prompt generation.")
    parser.add_argument("--read-input-list", action="store_true", dest="read_input_list", default=None, help="Read all source images from input/.")
    parser.add_argument("--single-image", action="store_false", dest="read_input_list", help="Process only --image.")
    parser.add_argument(
        "--prefer-loving-kindness-tone",
        action="store_true",
        dest="prefer_loving_kindness_tone",
        default=None,
        help="Where appropriate, gently bias prompts toward loving-kindness through light, color, and environment.",
    )
    parser.add_argument(
        "--no-loving-kindness-tone",
        action="store_false",
        dest="prefer_loving_kindness_tone",
        help="Disable the loving-kindness tonal bias even if it is enabled in config.",
    )
    parser.add_argument("--continue-after-failure", action="store_true", dest="continue_after_failure", default=None, help="Continue with the next input image after a failed stage.")
    parser.add_argument("--stop-after-failure", action="store_false", dest="continue_after_failure", help="Stop the pipeline after a failed stage.")
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile/grok-web"), help="Persistent Chrome profile for Grok Web.")
    parser.add_argument("--target-url", type=str, default="https://grok.com/imagine", help="Grok Web URL.")
    parser.add_argument("--chrome-exe", type=Path, default=None, help="Optional explicit Chrome executable path.")
    parser.add_argument("--chrome-debug-port", type=int, default=None, help="Optional Chrome remote debugging port.")
    parser.add_argument("--result-timeout", type=float, default=600.0, help="How long to wait for each generated video, in seconds.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="How long to wait for Grok Web to open, in seconds.")
    parser.add_argument("--upload-timeout", type=float, default=180.0, help="How long to wait for image upload readiness before submit, in seconds.")
    parser.add_argument("--no-submit", action="store_true", help="Prepare Grok forms without submitting them.")
    return parser.parse_args()


def build_generation_config(args: argparse.Namespace) -> GenerationConfig:
    config = load_generation_config(args.config_file)
    return config.override(
        generate_final_frames=getattr(args, "generate_final_frames", None),
        read_input_list=getattr(args, "read_input_list", None),
        generate_music=getattr(args, "generate_music", None),
        motion_model=getattr(args, "motion_model", None),
        generate_source_background=getattr(args, "generate_source_background", None),
        save_grok_debug_artifacts=getattr(args, "save_grok_debug_artifacts", None),
        generate_video=getattr(args, "generate_video", None),
        continue_after_failure=getattr(args, "continue_after_failure", None),
        prefer_loving_kindness_tone=getattr(args, "prefer_loving_kindness_tone", None),
    )


def _remove_processed_input(image_path: Path, settings: Settings) -> None:
    try:
        if image_path.parent.resolve() == settings.input_dir.resolve() and image_path.exists():
            image_path.unlink()
    except OSError:
        pass


def _handle_failure(
    *,
    settings: Settings,
    stage_id: str,
    image_path: Path,
    continue_after_failure: bool,
    error: Exception,
) -> None:
    error_output_dir = settings.project_root / "error" / "output" / stage_id
    if continue_after_failure:
        move_input_files_to_error(settings, stage_id, [image_path])
    else:
        remaining_inputs = [path for path in settings.input_dir.iterdir() if path.is_file()]
        move_files_to_directory(remaining_inputs, settings.project_root / "error" / "input" / stage_id)
    move_output_stage_to_error(settings, stage_id)
    error_output_dir.mkdir(parents=True, exist_ok=True)
    error_report = error_output_dir / f"{stage_id}_error.txt"
    error_report.write_text(
        "".join(
            [
                f"Stage failed: {stage_id}\n",
                f"Input image: {image_path}\n",
                f"Error type: {type(error).__name__}\n",
                f"Error message: {error}\n\n",
                "Traceback:\n",
                "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            ]
        ),
        encoding="utf-8",
    )
    print(f"Stage failed and was moved to error folders: {stage_id}", flush=True)
    print(f"Error report saved to: {error_report}", flush=True)
    clear_directory_contents(settings.output_dir)


def run_full_pipeline(args: argparse.Namespace, settings: Settings | None = None) -> list[Path]:
    settings = settings or Settings()
    settings.ensure_output()
    generation_config = build_generation_config(args)
    input_images = resolve_input_images(args, settings, generation_config)
    results: list[Path] = []
    total = len(input_images)
    grok_session_runner = GrokWebSessionRunner()

    try:
        for index, image_path in enumerate(input_images, start=1):
            clear_directory_contents(settings.output_dir)
            run_args = SimpleNamespace(**vars(args))
            run_args.image = image_path
            run_args.stage_id = stage_id_for_image(args.stage_id, image_path, index, total)
            stage_id = stage_identifier(run_args.stage_id, image_path)
            run_args.stage_id = stage_id

            try:
                metadata = _run_generation(
                    run_args,
                    generation_config,
                    settings=settings,
                    generate_video=generation_config.generate_video,
                    generate_styled_images=args.generate_styled_images,
                    generate_final_frames=generation_config.generate_final_frames,
                )

                manifest_path = write_pipeline_manifest(
                    settings,
                    stage_id,
                    image_path,
                    generation_config,
                    generate_final_frames=generation_config.generate_final_frames,
                    generate_styled_images=args.generate_styled_images,
                    generate_video=generation_config.generate_video,
                    model_name=args.model,
                    prompt_model=args.prompt_model,
                    motion_model=generation_config.motion_model,
                )
                sync_stage_non_video_assets(settings, generation_config, stage_id)
                print(f"API pipeline manifest saved to: {manifest_path}")

                if generation_config.generate_music:
                    music_prompt_file = _write_music_prompt(settings, stage_id, metadata)
                    sync_stage_non_video_assets(settings, generation_config, stage_id)
                    print(f"Music prompt saved: {music_prompt_file}")

                grok_args = SimpleNamespace(
                    prompt_dir=settings.output_dir,
                    input_dir=settings.input_dir,
                    config_file=args.config_file,
                    profile_dir=args.profile_dir,
                    target_url=args.target_url,
                    chrome_exe=args.chrome_exe,
                    chrome_debug_port=None,
                    result_timeout=args.result_timeout,
                    launch_timeout=args.launch_timeout,
                    upload_timeout=args.upload_timeout,
                    generate_video=generation_config.generate_video,
                    generate_source_background=generation_config.generate_source_background,
                    save_grok_debug_artifacts=generation_config.save_grok_debug_artifacts,
                    no_submit=args.no_submit,
                    skip_existing=False,
                    keep_workdirs=True,
                )
                def stage_runner(run_args: argparse.Namespace) -> Path:
                    return run_generation(run_args, settings=settings, runner=grok_session_runner.run)

                print("Starting Grok for current image...", flush=True)
                stage_outputs = run_batch(grok_args, settings=settings, runner=stage_runner)
                results.extend(stage_outputs)
                print("Closing Grok for current image...", flush=True)
                grok_session_runner.close_stage_session()
                if index < total:
                    print("Grok closed. Starting next image...", flush=True)
                else:
                    print("Grok closed.", flush=True)

                _remove_processed_input(image_path, settings)
                clear_directory_contents(settings.output_dir)
            except Exception as exc:
                print("Closing Grok for current image...", flush=True)
                grok_session_runner.close_stage_session()
                _handle_failure(
                    settings=settings,
                    stage_id=stage_id,
                    image_path=image_path,
                    continue_after_failure=generation_config.continue_after_failure,
                    error=exc,
                )
                if not generation_config.continue_after_failure:
                    raise
    finally:
        grok_session_runner.close()

    return results


def main() -> None:
    _configure_stdio()
    args = parse_args()
    outputs = run_full_pipeline(args)
    print(f"Processed output files: {len(outputs)}")


if __name__ == "__main__":
    try:
        main()
    except ConfigValidationError as exc:
        raise SystemExit(f"Config validation error: {exc}") from exc
