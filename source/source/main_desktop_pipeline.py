from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

from config import ConfigValidationError, GenerationConfig, Settings, load_generation_config
from main import _configure_stdio, _now_in_jerusalem, _run_generation, _write_music_prompt
from utils.project_delivery import resolve_delivery_dir, sync_stage_non_video_assets

SUPPORTED_INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="API pipeline for final frames: generate prompts locally and create final frames through OpenAI Images API."
    )
    parser.add_argument("--image", "-i", type=Path, required=False, help="Path to the source image (frame A).")
    parser.add_argument("--stage-id", "-s", type=str, default=None, help="Optional fixed stage identifier.")
    parser.add_argument("--config-file", type=Path, default=None, help="Optional generation config JSON.")
    parser.add_argument("--model", "-m", type=str, default=os.getenv("OPENAI_IMAGE_MODEL", "dall-e-2"), help="OpenAI image edit model.")
    parser.add_argument("--scene-model", type=str, default=None, help="Optional OpenAI vision model for scene analysis.")
    parser.add_argument("--prompt-model", type=str, default="gpt-4.1-mini", help="OpenAI text model for prompt synthesis.")
    parser.add_argument("--motion-model", type=str, default=None, help="OpenAI text model for AI motion selection.")
    parser.add_argument(
        "--generate-source-background",
        action="store_true",
        dest="generate_source_background",
        default=None,
        help="Create a background-generation prompt for each source image and enable Grok background generation.",
    )
    parser.add_argument(
        "--skip-source-background",
        action="store_false",
        dest="generate_source_background",
        help="Do not create source-background prompts.",
    )
    parser.add_argument(
        "--generate-video",
        action="store_true",
        dest="generate_video",
        default=None,
        help="Enable video generation in the manifest and downstream pipeline.",
    )
    parser.add_argument(
        "--skip-video",
        action="store_false",
        dest="generate_video",
        help="Disable video generation and keep only non-video stages.",
    )
    parser.add_argument("--generate-music", action="store_true", dest="generate_music", default=None, help="Generate a music prompt.")
    parser.add_argument("--skip-music", action="store_false", dest="generate_music", help="Do not generate a music prompt.")
    parser.add_argument("--generate-styled-images", action="store_true", help="Generate extra style variations from the original image.")
    parser.add_argument("--generate-final-frames", action="store_true", dest="generate_final_frames", default=None, help="Generate final frames through the API.")
    parser.add_argument("--skip-final-frames", action="store_false", dest="generate_final_frames", help="Do not call the API for final frame generation.")
    parser.add_argument("--read-input-list", action="store_true", dest="read_input_list", default=None, help="Read source frames as a list from input/.")
    parser.add_argument("--single-image", action="store_false", dest="read_input_list", help="Process only --image and ignore input/ list mode.")
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
    return parser.parse_args()


def stage_identifier(provided: str | None, image_path: Path | None = None) -> str:
    if provided:
        return provided
    prefix = image_path.stem if image_path is not None else "input"
    return f"{prefix}_{_now_in_jerusalem().strftime('%Y%m%d_%H%M%S')}"


def build_generation_config(args: argparse.Namespace) -> GenerationConfig:
    config = load_generation_config(args.config_file)
    return config.override(
        generate_final_frames=getattr(args, "generate_final_frames", None),
        read_input_list=getattr(args, "read_input_list", None),
        generate_music=getattr(args, "generate_music", None),
        motion_model=getattr(args, "motion_model", None),
        generate_source_background=getattr(args, "generate_source_background", None),
        generate_video=getattr(args, "generate_video", None),
        prefer_loving_kindness_tone=getattr(args, "prefer_loving_kindness_tone", None),
    )


def list_input_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES)


def resolve_input_images(args: argparse.Namespace, settings: Settings, generation_config: GenerationConfig) -> list[Path]:
    if args.image is not None:
        if args.image.is_dir():
            images = list_input_images(args.image)
            if not images:
                raise FileNotFoundError(f"No supported images found in: {args.image}")
            return images
        return [args.image]
    if generation_config.read_input_list:
        images = list_input_images(settings.input_dir)
        if not images:
            raise FileNotFoundError(f"No source frames found in input/: {settings.input_dir}")
        return images
    raise ValueError("Provide --image or enable read_input_list in config.")


def stage_id_for_image(base_stage_id: str | None, image_path: Path, index: int, total: int) -> str | None:
    if total == 1:
        return base_stage_id
    suffix = f"{index:03d}_{image_path.stem}"
    if base_stage_id:
        return f"{base_stage_id}_{suffix}"
    return None


def write_pipeline_manifest(
    settings: Settings,
    stage_id: str,
    image_path: Path,
    generation_config: GenerationConfig,
    *,
    generate_final_frames: bool,
    generate_styled_images: bool,
    generate_video: bool,
    model_name: str,
    prompt_model: str,
    motion_model: str,
) -> Path:
    steps: list[dict[str, object]] = []
    prompt_index = 1
    for framing_mode in generation_config.framing_modes():
        current_input = image_path
        for _branch_video_index in range(1, generation_config.video_count + 1):
            final_frame_path = settings.output_dir / f"{stage_id}_final_frame_{prompt_index}.png"
            final_frame_prompt_path = settings.output_dir / f"{stage_id}_final_frame_prompt_{prompt_index}.txt"
            step_entry = {
                "index": prompt_index,
                "framing_mode": framing_mode.value,
                "input_image": str(current_input),
                "v_prompt_file": str(settings.output_dir / f"{stage_id}_v_prompt_{prompt_index}.txt"),
                "v_prm_ru_file": str(settings.output_dir / f"{stage_id}_v_prm_ru_{prompt_index}.txt"),
                "final_frame_prompt_file": str(final_frame_prompt_path),
                "final_frame_prompt_created": final_frame_prompt_path.exists(),
                "final_frame_image": str(final_frame_path),
                "final_frame_exists": final_frame_path.exists(),
            }
            steps.append(step_entry)
            current_input = final_frame_path
            prompt_index += 1

    manifest = {
        "stage_id": stage_id,
        "pipeline": "api-final-frames",
        "initial_image": str(image_path),
        "model": model_name,
        "config": {
            "video_count": generation_config.video_count,
            "total_video_outputs": generation_config.total_video_outputs(),
            "camera_segments": generation_config.camera_segments,
            "motion_source": generation_config.motion_source.value,
            "write_description": generation_config.write_description,
            "generate_music": generation_config.generate_music,
            "generate_source_background": generation_config.generate_source_background,
            "prefer_face_closeups": generation_config.prefer_face_closeups,
            "use_ai_optimal_framing": generation_config.use_ai_optimal_framing,
            "generate_dual_framing_videos": generation_config.generate_dual_framing_videos,
            "hide_phone_in_selfie": generation_config.hide_phone_in_selfie,
            "prefer_loving_kindness_tone": generation_config.prefer_loving_kindness_tone,
            "framing_modes": [mode.value for mode in generation_config.framing_modes()],
            "prompt_model": prompt_model,
            "motion_model": motion_model,
            "final_videos_dir": str(resolve_delivery_dir(settings, generation_config.final_videos_dir)),
            "regeneration_assets_dir": str(resolve_delivery_dir(settings, generation_config.regeneration_assets_dir)),
        },
        "artifacts": {
            "description": str(settings.output_dir / f"{stage_id}_description.txt"),
            "scene_analysis": str(settings.output_dir / f"{stage_id}_scene_analysis.json"),
            "bg_prompt": str(settings.output_dir / f"{stage_id}_bg_prompt.txt") if generation_config.generate_source_background else None,
            "bg_prm_ru": str(settings.output_dir / f"{stage_id}_bg_prm_ru.txt") if generation_config.generate_source_background else None,
            "assoc_bg_prompt": str(settings.output_dir / f"{stage_id}_assoc_bg_prompt.txt") if generation_config.generate_source_background else None,
            "assoc_bg_prm_ru": str(settings.output_dir / f"{stage_id}_assoc_bg_prm_ru.txt") if generation_config.generate_source_background else None,
            "bg_image": str(settings.output_dir / f"{stage_id}_bg_image_16x9.png") if generation_config.generate_source_background else None,
            "m_prompt": str(settings.output_dir / f"{stage_id}_m_prompt.txt"),
            "generate_final_frames": generate_final_frames,
            "generate_styled_images": generate_styled_images,
            "generate_video": generate_video,
            "generate_music": generation_config.generate_music,
        },
        "steps": steps,
    }
    manifest_path = settings.output_dir / f"{stage_id}_api_pipeline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def main() -> None:
    _configure_stdio()
    args = parse_args()
    settings = Settings()
    settings.ensure_output()

    os.environ["OPENAI_IMAGE_MODEL"] = args.model
    generation_config = build_generation_config(args)
    input_images = resolve_input_images(args, settings, generation_config)
    total = len(input_images)
    last_stage_id: str | None = None
    last_metadata = None

    for index, image_path in enumerate(input_images, start=1):
        run_args = SimpleNamespace(**vars(args))
        run_args.image = image_path
        run_args.stage_id = stage_id_for_image(args.stage_id, image_path, index, total)
        stage_id = stage_identifier(run_args.stage_id, image_path)
        run_args.stage_id = stage_id
        last_stage_id = stage_id

        last_metadata = _run_generation(
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

    if generation_config.generate_music and last_stage_id and last_metadata is not None:
        music_prompt_file = _write_music_prompt(settings, last_stage_id, last_metadata)
        sync_stage_non_video_assets(settings, generation_config, last_stage_id)
        print(f"Music prompt saved to: {music_prompt_file}")


if __name__ == "__main__":
    try:
        main()
    except ConfigValidationError as exc:
        raise SystemExit(f"Config validation error: {exc}") from exc
