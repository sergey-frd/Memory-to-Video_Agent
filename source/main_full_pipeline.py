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
    remove_path,
    sync_stage_non_video_assets,
    sync_video_file,
    resolve_delivery_dir,
)

from utils.grok_workspace import workspace, save_state, delivered_outputs, digest, clean_work, completed_image, record_completed


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
    parser.add_argument("--require-grok-debug-port", action="store_true", help="Fail instead of opening a fallback Grok Chrome window when --chrome-debug-port cannot be used.")
    parser.add_argument("--reuse-existing-grok-page", action="store_true", help="With --chrome-debug-port, reuse the current Grok page instead of navigating before each Grok request.")
    parser.add_argument("--result-timeout", type=float, default=600.0, help="How long to wait for each generated video, in seconds.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="How long to wait for Grok Web to open, in seconds.")
    parser.add_argument("--upload-timeout", type=float, default=180.0, help="How long to wait for image upload readiness before submit, in seconds.")
    parser.add_argument("--no-submit", action="store_true", help="Prepare Grok forms without submitting them.")
    parser.add_argument("--force-regenerate", action="store_true", help="Ignore completed-image records and create a new generation session.")
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


def _remove_processed_input(image_path: Path, settings: Settings) -> bool:
    try:
        same_input_queue = image_path.parent.resolve() == settings.input_dir.resolve()
    except OSError:
        same_input_queue = False
    if not same_input_queue or not image_path.exists():
        return True
    try:
        remove_path(image_path)
    except OSError:
        return False
    return not image_path.exists()


def _flush_processed_input_queue(image_paths: list[Path], settings: Settings) -> list[Path]:
    remaining: list[Path] = []
    for image_path in image_paths:
        if not _remove_processed_input(image_path, settings) and image_path.exists():
            remaining.append(image_path)
    return remaining


def _handle_failure(
    *,
    settings: Settings,
    stage_id: str,
    image_path: Path,
    continue_after_failure: bool,
    error: Exception,
) -> None:
    error_output_dir = settings.project_root / "error" / "output" / stage_id
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
    print(f"Stage failed; input and workspace preserved for retry: {stage_id}", flush=True)
    print(f"Error report saved to: {error_report}", flush=True)


def run_full_pipeline(args: argparse.Namespace, settings: Settings | None = None) -> list[Path]:
    settings = settings or Settings()
    settings.ensure_output()
    generation_config = build_generation_config(args)
    if args.image is None and generation_config.read_input_list and not any(
            p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
            for p in settings.input_dir.glob('*')):
        print("Input queue is empty; nothing to generate.", flush=True)
        return []
    input_images = resolve_input_images(args, settings, generation_config)
    delivery_dir = resolve_delivery_dir(settings, generation_config.final_videos_dir)
    force = getattr(args, 'force_regenerate', False)
    from uuid import uuid4
    force_key = uuid4().hex if force else None
    base_settings = settings
    results: list[Path] = []
    pending_input_cleanup: list[Path] = []
    total = len(input_images)
    grok_session_runner = GrokWebSessionRunner()

    try:
        for index, image_path in enumerate(input_images, start=1):
            completed = None if force or args.no_submit else completed_image(base_settings, image_path, delivery_dir)
            if completed is not None:
                results.extend(completed)
                print(f"Skipped completed image: {image_path}", flush=True)
                pending_input_cleanup.append(image_path)
                continue
            run_args = SimpleNamespace(**vars(args))
            run_args.image = image_path
            run_args.stage_id = stage_id_for_image(args.stage_id, image_path, index, total)
            stage_id = stage_identifier(run_args.stage_id, image_path)
            settings, state_path, state = workspace(base_settings, image_path, {
                "config": vars(generation_config), "model": args.model,
                "prompt_model": args.prompt_model, "scene_model": getattr(args, "scene_model", None),
                "styled": args.generate_styled_images, "stage_id": args.stage_id,
                **({"force_run": force_key} if force else {}),
            })
            stage_id = state.setdefault("stage_id", stage_id)
            run_args.stage_id = stage_id
            save_state(state_path, state)
            completed = delivered_outputs(state)
            if completed is not None and not args.no_submit:
                results.extend(completed)
                print(f"Skipped delivered image: {image_path}", flush=True)
                record_completed(base_settings, image_path, delivery_dir, completed)
                pending_input_cleanup.append(image_path)
                continue

            try:
                if not state.get("prompts_ready"):
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

                    state["prompts_ready"] = True
                    save_state(state_path, state)

                grok_args = SimpleNamespace(
                    prompt_dir=settings.output_dir,
                    input_dir=image_path.parent,
                    source_image=image_path,
                    config_file=args.config_file,
                    profile_dir=args.profile_dir,
                    target_url=args.target_url,
                    chrome_exe=args.chrome_exe,
                    chrome_debug_port=args.chrome_debug_port,
                    require_debug_port=getattr(args, "require_grok_debug_port", False),
                    reuse_existing_page=getattr(args, "reuse_existing_grok_page", False),
                    result_timeout=args.result_timeout,
                    launch_timeout=args.launch_timeout,
                    upload_timeout=args.upload_timeout,
                    generate_video=generation_config.generate_video,
                    generate_source_background=generation_config.generate_source_background,
                    save_grok_debug_artifacts=generation_config.save_grok_debug_artifacts,
                    no_submit=args.no_submit,
                    skip_existing=True,
                    keep_workdirs=True,
                )
                def stage_runner(run_args: argparse.Namespace) -> Path:
                    return run_generation(run_args, settings=settings, runner=grok_session_runner.run)

                print("Starting Grok for current image...", flush=True)
                if not generation_config.generate_video and not generation_config.generate_source_background:
                    print(
                        "Warning: generate_video=false and generate_source_background=false; "
                        "Grok will not submit any prompts unless you enable one of them in config.",
                        flush=True,
                    )
                stage_outputs = run_batch(grok_args, settings=settings, runner=stage_runner)
                if args.no_submit:
                    grok_session_runner.close_stage_session()
                    results.extend(stage_outputs)
                    continue
                if not stage_outputs:
                    raise RuntimeError("No delivered outputs; preserving input and workspace")
                delivered = []
                for output in stage_outputs:
                    if not output.is_file() or output.stat().st_size == 0:
                        raise RuntimeError(f"Missing or empty output: {output}")
                    target = sync_video_file(settings, generation_config, output).resolve()
                    if digest(target) != digest(output):
                        raise RuntimeError(f"Delivery verification failed: {target}")
                    if target.is_relative_to(settings.output_dir):
                        raise ValueError("Delivery directory must be outside the temporary workspace")
                    delivered.append({"path": str(target), "sha256": digest(target)})
                sync_stage_non_video_assets(settings, generation_config, stage_id)
                record_completed(base_settings, image_path, delivery_dir, [Path(item["path"]) for item in delivered])
                state.update(complete=True, outputs=delivered)
                save_state(state_path, state)
                results.extend(Path(item["path"]) for item in delivered)
                clean_work(settings.output_dir, state_path)
                state["prompts_ready"] = False
                save_state(state_path, state)
                print("Closing Grok for current image...", flush=True)
                grok_session_runner.close_stage_session()
                if index < total:
                    print("Grok closed. Starting next image...", flush=True)
                else:
                    print("Grok closed.", flush=True)

                pending_input_cleanup.append(image_path)
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
        remaining_inputs = _flush_processed_input_queue(pending_input_cleanup, base_settings)
        if remaining_inputs:
            remaining_inputs = _flush_processed_input_queue(remaining_inputs, base_settings)
        if remaining_inputs:
            remaining_label = ", ".join(path.name for path in remaining_inputs)
            print(f"Warning: processed input files could not be removed from queue: {remaining_label}", flush=True)

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
