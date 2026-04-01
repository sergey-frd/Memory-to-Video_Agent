from __future__ import annotations

import argparse
import ctypes
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import cycle
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from api.openai_image import edit_image_with_openai
from api.openai_motion_selector import select_motion_sequences_with_openai
from api.openai_prompt_synthesizer import synthesize_background_prompt_with_openai, synthesize_prompt_bundle_with_openai
from api.openai_scene import analyze_scene_with_openai
from config import (
    ConfigValidationError,
    GenerationConfig,
    MotionSource,
    Settings,
    VideoFramingMode,
    load_generation_config,
)
from models.scene_analysis import SceneAnalysis
from utils.camera_movements import CameraMovementSets, load_camera_movements
from utils.image_analysis import ImageMetadata, analyze_image
from utils.project_delivery import sync_stage_non_video_assets
from utils.prompt_builder import BackgroundPromptBundle, PromptBuilder, PromptBundle

SUPPORTED_INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _jerusalem_timezone():
    try:
        return ZoneInfo("Asia/Jerusalem")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=2), name="Asia/Jerusalem")


JERUSALEM_TZ = _jerusalem_timezone()


def _configure_stdio() -> None:
    """Switch Windows console and Python streams to UTF-8 when possible."""
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


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline generator for prompts and video assets.")
    parser.add_argument("--image", "-i", type=Path, required=False, help="Path to the source frame A.")
    parser.add_argument("--stage-id", "-s", type=str, help="Optional custom stage identifier.")
    parser.add_argument(
        "--generate-video",
        action="store_true",
        dest="generate_video",
        default=None,
        help="Enable video generation.",
    )
    parser.add_argument(
        "--skip-video",
        action="store_false",
        dest="generate_video",
        help="Disable video generation.",
    )
    parser.add_argument(
        "--generate-music",
        action="store_true",
        dest="generate_music",
        default=None,
        help="Generate music prompt after all source files are processed.",
    )
    parser.add_argument(
        "--skip-music",
        action="store_false",
        dest="generate_music",
        help="Explicitly skip music prompt generation.",
    )
    parser.add_argument(
        "--generate-styled-images",
        "-g",
        action="store_true",
        help="Generate style variants from the source image.",
    )
    parser.add_argument(
        "--generate-final-frames",
        action="store_true",
        dest="generate_final_frames",
        default=None,
        help="Generate final frames through the image API.",
    )
    parser.add_argument(
        "--skip-final-frames",
        action="store_false",
        dest="generate_final_frames",
        help="Explicitly skip final frame generation.",
    )
    parser.add_argument("--config-file", type=Path, default=None, help="Optional generation config JSON file.")
    parser.add_argument("--scene-model", type=str, default=None, help="Optional OpenAI vision model for scene analysis.")
    parser.add_argument(
        "--prompt-model",
        type=str,
        default="gpt-4.1-mini",
        help="OpenAI text model for prompt synthesis.",
    )
    parser.add_argument(
        "--motion-model",
        type=str,
        default=None,
        help="OpenAI text model for AI camera motion selection.",
    )
    parser.add_argument(
        "--generate-source-background",
        action="store_true",
        dest="generate_source_background",
        default=None,
        help="Create a background-generation prompt for the source image and enable Grok background generation.",
    )
    parser.add_argument(
        "--skip-source-background",
        action="store_false",
        dest="generate_source_background",
        help="Do not create or use a source-background prompt.",
    )
    parser.add_argument("--videos", type=int, help="How many videos to generate from one source frame.")
    parser.add_argument("--segments", type=int, help="How many camera motion segments each video should use.")
    parser.add_argument(
        "--motion-source",
        choices=[motion.value for motion in MotionSource],
        help="Motion source selection.",
    )
    parser.add_argument("--no-description", action="store_true", help="Skip description file generation.")
    parser.add_argument(
        "--read-input-list",
        action="store_true",
        dest="read_input_list",
        default=None,
        help="Read source frames as a list from input/.",
    )
    parser.add_argument(
        "--single-image",
        action="store_false",
        dest="read_input_list",
        help="Process only --image and ignore input/ list mode.",
    )
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


def _now_in_jerusalem() -> datetime:
    return datetime.now(JERUSALEM_TZ)


def _stage_identifier(provided: str | None, image_path: Path | None = None) -> str:
    if provided:
        return provided
    prefix = image_path.stem if image_path is not None else "input"
    timestamp = _now_in_jerusalem().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}"


def _build_generation_config(args: argparse.Namespace, settings: Settings) -> GenerationConfig:
    default_path = settings.project_root / "config.json"
    config_path = args.config_file or (default_path if default_path.exists() else None)
    config = load_generation_config(config_path)
    overrides = {
        "video_count": args.videos,
        "generate_video": getattr(args, "generate_video", None),
        "camera_segments": args.segments,
        "motion_source": args.motion_source,
        "motion_model": getattr(args, "motion_model", None),
        "generate_source_background": getattr(args, "generate_source_background", None),
        "write_description": False if getattr(args, "no_description", False) else None,
        "generate_final_frames": getattr(args, "generate_final_frames", None),
        "read_input_list": getattr(args, "read_input_list", None),
        "generate_music": getattr(args, "generate_music", None),
        "prefer_loving_kindness_tone": getattr(args, "prefer_loving_kindness_tone", None),
    }
    return config.override(**overrides)


def _list_input_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES)


def _resolve_input_images(args: argparse.Namespace, settings: Settings, generation_config: GenerationConfig) -> list[Path]:
    if args.image is not None:
        if args.image.is_dir():
            images = _list_input_images(args.image)
            if not images:
                raise FileNotFoundError(f"No supported images found in directory: {args.image}")
            return images
        return [args.image]
    if generation_config.read_input_list:
        images = _list_input_images(settings.input_dir)
        if not images:
            raise FileNotFoundError(f"No source frames found in input/: {settings.input_dir}")
        return images
    raise ValueError("Provide --image or enable read_input_list.")


def _stage_id_for_image(base_stage_id: str | None, image_path: Path, index: int, total: int) -> str | None:
    if total == 1:
        return base_stage_id
    suffix = f"{index:03d}_{image_path.stem}"
    if base_stage_id:
        return f"{base_stage_id}_{suffix}"
    return None


def _read_styles(styles_path: Path) -> list[str]:
    text = styles_path.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _write_prompt(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_prompt_log(
    path: Path,
    prompt_text: str,
    source_hash: str | None = None,
    final_hash: str | None = None,
    identical: bool | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [f"Timestamp: {_now_in_jerusalem().isoformat()}", "", prompt_text]
    if source_hash:
        log_lines.extend(["", f"Source SHA256: {source_hash}"])
    if final_hash:
        log_lines.append(f"Final frame SHA256: {final_hash}")
    if identical is not None:
        log_lines.append(f"Comparison: {'identical' if identical else 'different'}")
    path.write_text("\n".join(log_lines), encoding="utf-8")


def _write_scene_analysis_json(path: Path, scene_analysis: SceneAnalysis) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scene_analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_description_file(
    image_path: Path,
    metadata: ImageMetadata,
    destination: Path,
    initial_description: str,
    motion_sequence: list[str] | None,
    camera_sets: CameraMovementSets | None,
    scene_analysis: SceneAnalysis | None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ratio = metadata.width / metadata.height if metadata.height else 1.0
    orientation_label = {
        "portrait": "vertical portrait frame",
        "landscape": "horizontal landscape frame",
        "square": "square frame",
    }.get(metadata.orientation, "neutral frame orientation")
    format_lines = [
        f"Image format: {orientation_label} ({metadata.width}x{metadata.height}, about {ratio:.2f}:1).",
        "This format must be preserved in video and final frames.",
    ]
    composition_lines = [
        "Scene composition:",
        f"- Input anchor: {initial_description}.",
        f"- Scene summary: {metadata.scene_summary}",
        f"- Composition profile: {metadata.composition_label}.",
        f"- Depth profile: {metadata.depth_label}.",
    ]
    if scene_analysis:
        composition_lines.extend(
            [
                f"- Narrative summary: {scene_analysis.summary}",
                f"- Visible people count: {scene_analysis.people_count}.",
            ]
        )
        if scene_analysis.background:
            composition_lines.append(f"- Background: {scene_analysis.background}")
        if scene_analysis.shot_type:
            composition_lines.append(f"- Framing: {scene_analysis.shot_type}")
        if scene_analysis.main_action:
            composition_lines.append(f"- Main action: {scene_analysis.main_action}")
        if scene_analysis.mood:
            composition_lines.append(f"- Mood: {', '.join(scene_analysis.mood)}")
        if scene_analysis.relationships:
            composition_lines.append(f"- Relationship dynamic: {'; '.join(scene_analysis.relationships)}")
    visual_lines = [
        "Visual features:",
        f"- Palette: {metadata.palette_label}.",
        f"- Lighting: {metadata.brightness_label}.",
        f"- Tonality: {metadata.contrast_label}.",
        f"- Atmosphere: {metadata.atmosphere_label}.",
    ]
    if scene_analysis and scene_analysis.people:
        visual_lines.append("Observed characters:")
        for person in scene_analysis.people:
            bits = [person.label]
            if person.position_in_frame:
                bits.append(f"at {person.position_in_frame}")
            if person.role_in_scene:
                bits.append(f"reading as {person.role_in_scene}")
            details = [
                bit
                for bit in [
                    person.apparent_age_group,
                    person.apparent_gender_presentation,
                    person.facial_expression,
                    person.clothing,
                    person.pose,
                ]
                if bit
            ]
            line = " ".join(bit for bit in bits if bit)
            if details:
                line = f"{line}: {', '.join(details)}"
            visual_lines.append(f"- {line}")

    motion_source_label = "FULL LIST 3 DISTANCE"
    if camera_sets and camera_sets.nearby and motion_sequence and motion_sequence[0] in camera_sets.nearby:
        motion_source_label = "FULL LIST 3 NEARBY"

    logic_lines = ["Cinematic motion logic:", "- This frame works best when:"]
    if motion_sequence:
        first_motion = motion_sequence[0]
        second_motion = motion_sequence[1] if len(motion_sequence) > 1 else motion_sequence[0]
        context_anchor = scene_analysis.background if scene_analysis and scene_analysis.background else metadata.composition_label
        emotional_anchor = ", ".join(scene_analysis.mood) if scene_analysis and scene_analysis.mood else metadata.atmosphere_label
        logic_lines.append(f"- first the scene context is opened from {context_anchor} using {first_motion.lower()}.")
        logic_lines.append(f"- then the view shifts toward {emotional_anchor} using {second_motion.lower()}.")
        logic_lines.append(f"From {motion_source_label} the best options are:")
        logic_lines.append(f"- First half of the video: {first_motion}.")
        logic_lines.append(f"- Second half of the video: {second_motion}.")
    else:
        logic_lines.append("- first show the structural context of the scene, then resolve toward the emotional center.")

    description = "\n".join([
        "Source image analysis (FRAME A)",
        "",
        *format_lines,
        "",
        *composition_lines,
        "",
        *visual_lines,
        "",
        *logic_lines,
    ])
    destination.write_text(description, encoding="utf-8")


def _write_music_prompt(settings: Settings, stage_id: str, metadata: ImageMetadata) -> Path:
    music_prompt_file = settings.output_dir / f"{stage_id}_m_prompt.txt"
    _write_prompt(music_prompt_file, _music_prompt_text(metadata, stage_id))
    return music_prompt_file


def _motion_family(motion: str) -> str:
    text = motion.lower()
    family_keywords = {
        "push_in": ("приближ", "наезд", "zoom in", "push in", "dolly in", "move closer"),
        "pull_out": ("отдал", "zoom out", "pull out", "dolly out", "move back"),
        "orbit": ("orbit", "орбит", "облет", "полукруг", "arc around"),
        "pan": ("pan", "панорам", "смещен", "slide", "truck"),
        "tilt": ("tilt", "наклон", "подъем взгляда", "опускание взгляда"),
        "crane": ("crane", "кран", "boom", "jib"),
        "static": ("static", "locked", "статич", "фиксир"),
    }
    for family, keywords in family_keywords.items():
        if any(keyword in text for keyword in keywords):
            return family
    return "unknown"


def _enforce_motion_continuity(sequences: list[list[str]]) -> list[list[str]]:
    if len(sequences) < 2:
        return sequences

    normalized: list[list[str]] = [list(sequence) for sequence in sequences]
    for index in range(1, len(normalized)):
        previous_sequence = normalized[index - 1]
        current_sequence = normalized[index]
        if not previous_sequence or not current_sequence:
            continue
        previous_end = previous_sequence[-1].strip()
        current_start = current_sequence[0].strip()
        if not previous_end or not current_start:
            continue
        if _motion_family(previous_end) != _motion_family(current_start):
            current_sequence[0] = previous_end
    return normalized


def _materialize_motion_sequences(
    config: GenerationConfig,
    camera_sets,
    *,
    metadata: ImageMetadata | None = None,
    scene_analysis: SceneAnalysis | None = None,
    motion_model: str | None = None,
    motion_selector: MotionSelector | None = None,
    framing_mode: VideoFramingMode | None = None,
) -> list[list[str]]:
    resolved_framing_mode = framing_mode or config.primary_framing_mode()
    if config.motion_source == MotionSource.AI:
        if metadata is not None and scene_analysis is not None:
            selector = motion_selector or _default_motion_selector
            sequences = selector(
                metadata=metadata,
                scene_analysis=scene_analysis,
                video_count=config.video_count,
                camera_segments=config.camera_segments,
                framing_mode=resolved_framing_mode,
                model=motion_model,
            )
            return _enforce_motion_continuity(sequences)
        pool = [f"AI camera movement {i + 1}" for i in range(config.video_count * config.camera_segments + 4)]
    else:
        pool = [*camera_sets.nearby, *camera_sets.distance]
    if not pool:
        pool = ["standard cinematic motion"]
    total_needed = config.video_count * config.camera_segments
    if len(pool) < total_needed:
        pool = list(cycle(pool))[:total_needed]
    sequences: list[list[str]] = []
    for video_index in range(config.video_count):
        start = video_index * config.camera_segments
        sequence = [pool[(start + offset) % len(pool)] for offset in range(config.camera_segments)]
        sequences.append(sequence)
    return _enforce_motion_continuity(sequences)


def _materialize_video_plans(
    config: GenerationConfig,
    camera_sets,
    *,
    metadata: ImageMetadata | None = None,
    scene_analysis: SceneAnalysis | None = None,
    motion_model: str | None = None,
    motion_selector: MotionSelector | None = None,
) -> list[VideoGenerationPlan]:
    plans: list[VideoGenerationPlan] = []
    prompt_index = 1
    for framing_mode in config.framing_modes():
        sequences = _materialize_motion_sequences(
            config,
            camera_sets,
            metadata=metadata,
            scene_analysis=scene_analysis,
            motion_model=motion_model,
            motion_selector=motion_selector,
            framing_mode=framing_mode,
        )
        for branch_video_index, motion_sequence in enumerate(sequences, start=1):
            plans.append(
                VideoGenerationPlan(
                    framing_mode=framing_mode,
                    branch_video_index=branch_video_index,
                    prompt_index=prompt_index,
                    motion_sequence=motion_sequence,
                )
            )
            prompt_index += 1
    return plans


ImageEditor = Callable[[Path, str, Path, ImageMetadata, str, Optional[str]], Path]
SceneAnalyzer = Callable[[Path, str | None], SceneAnalysis]
PromptSynthesizer = Callable[..., PromptBundle]
BackgroundPromptSynthesizer = Callable[..., BackgroundPromptBundle]
MotionSelector = Callable[..., list[list[str]]]


@dataclass(frozen=True)
class VideoGenerationPlan:
    framing_mode: VideoFramingMode
    branch_video_index: int
    prompt_index: int
    motion_sequence: list[str]


def _default_scene_analyzer(image_path: Path, model: str | None) -> SceneAnalysis:
    return analyze_scene_with_openai(image_path, model=model, language="ru")


def _default_motion_selector(**kwargs: object) -> list[list[str]]:
    return select_motion_sequences_with_openai(**kwargs)


def _default_prompt_synthesizer(**kwargs: object) -> PromptBundle:
    return synthesize_prompt_bundle_with_openai(**kwargs)


def _default_background_prompt_synthesizer(**kwargs: object) -> BackgroundPromptBundle:
    return synthesize_background_prompt_with_openai(**kwargs)


def _run_generation(
    args: argparse.Namespace,
    generation_config: GenerationConfig,
    settings: Settings | None = None,
    image_editor: ImageEditor | None = None,
    scene_analyzer: SceneAnalyzer | None = None,
    motion_selector: MotionSelector | None = None,
    prompt_synthesizer: PromptSynthesizer | None = None,
    background_prompt_synthesizer: BackgroundPromptSynthesizer | None = None,
    generate_video: bool = True,
    generate_styled_images: bool = False,
    generate_final_frames: bool = True,
) -> ImageMetadata:
    settings = settings or Settings()
    settings.ensure_output()

    stage_id = _stage_identifier(args.stage_id, args.image)
    print(f"Stage id: {stage_id}")

    if not args.image.exists():
        raise FileNotFoundError(f"Input frame not found: {args.image}")

    current_frame = args.image
    current_metadata = analyze_image(current_frame)
    scene_model = getattr(args, "scene_model", None)
    prompt_model = getattr(args, "prompt_model", None)
    motion_model = getattr(args, "motion_model", None) or generation_config.motion_model
    analyzer = scene_analyzer or _default_scene_analyzer
    resolved_motion_selector = motion_selector or _default_motion_selector
    prompt_builder = prompt_synthesizer or _default_prompt_synthesizer
    resolved_background_prompt_synthesizer = background_prompt_synthesizer or _default_background_prompt_synthesizer
    current_scene = analyzer(current_frame, scene_model)
    scene_json_file = settings.output_dir / f"{stage_id}_scene_analysis.json"
    _write_scene_analysis_json(scene_json_file, current_scene)
    camera_sets = load_camera_movements(settings.services_dir)
    source_frame = current_frame
    source_metadata = current_metadata
    source_scene = current_scene
    video_plans = _materialize_video_plans(
        generation_config,
        camera_sets,
        metadata=source_metadata,
        scene_analysis=source_scene,
        motion_model=motion_model,
        motion_selector=resolved_motion_selector,
    )
    background_motion_sequence = [motion for plan in video_plans for motion in plan.motion_sequence]

    if generation_config.write_description:
        desc_file = settings.output_dir / f"{stage_id}_description.txt"
        initial_desc_txt = "frame A (source frame)"
        first_sequence = video_plans[0].motion_sequence if video_plans else []
        _write_description_file(source_frame, source_metadata, desc_file, initial_desc_txt, first_sequence, camera_sets, source_scene)
        print(f"Description file created: {desc_file.name}")

    if generation_config.generate_source_background:
        bg_prompt_file = settings.output_dir / f"{stage_id}_bg_prompt.txt"
        bg_prompt_ru_file = settings.output_dir / f"{stage_id}_bg_prm_ru.txt"
        assoc_bg_prompt_file = settings.output_dir / f"{stage_id}_assoc_bg_prompt.txt"
        assoc_bg_prompt_ru_file = settings.output_dir / f"{stage_id}_assoc_bg_prm_ru.txt"
        try:
            bg_bundle = resolved_background_prompt_synthesizer(
                metadata=source_metadata,
                scene_analysis=source_scene,
                stage_id=stage_id,
                motion_sequence=background_motion_sequence,
                prefer_loving_kindness_tone=generation_config.prefer_loving_kindness_tone,
                model=prompt_model,
            )
        except Exception:
            bg_bundle = PromptBuilder(
                source_metadata,
                stage_id,
                scene_analysis=source_scene,
                hide_phone_in_selfie=generation_config.hide_phone_in_selfie,
                prefer_loving_kindness_tone=generation_config.prefer_loving_kindness_tone,
            ).build_background_prompt_bundle(background_motion_sequence)
        _write_prompt(bg_prompt_file, bg_bundle.background_prompt)
        _write_prompt(bg_prompt_ru_file, bg_bundle.background_prompt_ru)
        _write_prompt(assoc_bg_prompt_file, bg_bundle.association_prompt)
        _write_prompt(assoc_bg_prompt_ru_file, bg_bundle.association_prompt_ru)
        print(f"Background prompt saved: {bg_prompt_file.name}")
        print(f"Russian background prompt saved: {bg_prompt_ru_file.name}")
        print(f"Association background prompt saved: {assoc_bg_prompt_file.name}")
        print(f"Russian association background prompt saved: {assoc_bg_prompt_ru_file.name}")

    branch_frame = source_frame
    branch_metadata = source_metadata
    branch_scene = source_scene
    last_metadata = source_metadata
    total_videos = len(video_plans)
    for plan in video_plans:
        if plan.branch_video_index == 1:
            branch_frame = source_frame
            branch_metadata = source_metadata
            branch_scene = source_scene
            initial_desc = "frame A (source frame)"
        else:
            initial_desc = "final frame from the previous video in the same framing mode"
        try:
            bundle = prompt_builder(
                metadata=branch_metadata,
                scene_analysis=branch_scene,
                stage_id=stage_id,
                prompt_index=plan.prompt_index,
                total_videos=total_videos,
                initial_frame_description=initial_desc,
                motion_sequence=plan.motion_sequence,
                framing_mode=plan.framing_mode,
                hide_phone_in_selfie=generation_config.hide_phone_in_selfie,
                prefer_loving_kindness_tone=generation_config.prefer_loving_kindness_tone,
                model=prompt_model,
            )
        except Exception:
            builder = PromptBuilder(
                branch_metadata,
                stage_id,
                scene_analysis=branch_scene,
                framing_mode=plan.framing_mode,
                hide_phone_in_selfie=generation_config.hide_phone_in_selfie,
                prefer_loving_kindness_tone=generation_config.prefer_loving_kindness_tone,
            )
            bundle = builder.build_video_prompt(
                prompt_index=plan.prompt_index,
                total_videos=total_videos,
                initial_frame_description=initial_desc,
                motion_sequence=plan.motion_sequence,
            )

        video_file = settings.output_dir / f"{stage_id}_v_prompt_{plan.prompt_index}.txt"
        _write_prompt(video_file, bundle.video_prompt)
        print(f"Video prompt #{plan.prompt_index} saved: {video_file.name}")

        video_ru_file = settings.output_dir / f"{stage_id}_v_prm_ru_{plan.prompt_index}.txt"
        _write_prompt(video_ru_file, bundle.video_prompt_ru)
        print(f"Russian video prompt #{plan.prompt_index} saved: {video_ru_file.name}")

        if generate_final_frames:
            final_frame_prompt_file = settings.output_dir / f"{stage_id}_final_frame_prompt_{plan.prompt_index}.txt"
            _write_prompt(final_frame_prompt_file, bundle.final_frame_prompt)
            print(f"Final frame prompt #{plan.prompt_index} saved: {final_frame_prompt_file.name}")
            final_frame_image = settings.output_dir / f"{stage_id}_final_frame_{plan.prompt_index}.png"
            editor = image_editor or edit_image_with_openai
            source_hash = _hash_file(branch_frame)
            editor(
                image_path=branch_frame,
                style=f"final-frame-{plan.prompt_index}",
                output_path=final_frame_image,
                metadata=branch_metadata,
                stage_id=stage_id,
                prompt_override=bundle.final_frame_prompt,
            )
            final_hash = _hash_file(final_frame_image)
            log_file = settings.output_dir / f"{stage_id}_final_frame_prompt_{plan.prompt_index}.log"
            _write_prompt_log(log_file, bundle.final_frame_prompt, source_hash=source_hash, final_hash=final_hash, identical=source_hash == final_hash)
            print(f"Final frame #{plan.prompt_index} generated: {final_frame_image.name}")
            if source_hash == final_hash:
                print("Warning: final frame hash matches the source frame.")
            branch_frame = final_frame_image
            branch_metadata = analyze_image(branch_frame)
            branch_scene = analyzer(branch_frame, scene_model)
            last_metadata = branch_metadata
            _write_scene_analysis_json(scene_json_file, branch_scene)

        if bundle.image_edit_prompt:
            edit_file = settings.output_dir / f"{stage_id}_image_edit_prompt_{plan.prompt_index}.txt"
            _write_prompt(edit_file, bundle.image_edit_prompt)
            print(f"Intermediate image prompt #{plan.prompt_index} saved: {edit_file.name}")

    if generate_styled_images:
        styles = _read_styles(settings.styles_file)
        editor = image_editor or edit_image_with_openai
        for style in styles:
            destination = settings.output_dir / f"{stage_id}_{args.image.stem}_{style}.png"
            editor(image_path=args.image, style=style, output_path=destination, metadata=source_metadata, stage_id=stage_id)
            print(f"Styled image saved: {destination.name}")

    if generate_video:
        print("Video generation is enabled as a placeholder only.")

    return last_metadata


def main() -> None:
    _configure_stdio()
    args = _parse_arguments()
    settings = Settings()
    generation_config = _build_generation_config(args, settings)
    input_images = _resolve_input_images(args, settings, generation_config)

    last_stage_id: str | None = None
    last_metadata: ImageMetadata | None = None
    total = len(input_images)
    for index, image_path in enumerate(input_images, start=1):
        run_args = SimpleNamespace(**vars(args))
        run_args.image = image_path
        run_args.stage_id = _stage_id_for_image(args.stage_id, image_path, index, total)
        last_stage_id = _stage_identifier(run_args.stage_id, image_path)
        run_args.stage_id = last_stage_id
        last_metadata = _run_generation(
            run_args,
            generation_config,
            settings=settings,
            generate_video=generation_config.generate_video,
            generate_styled_images=args.generate_styled_images,
            generate_final_frames=generation_config.generate_final_frames,
        )
        sync_stage_non_video_assets(settings, generation_config, last_stage_id)

    if generation_config.generate_music and last_stage_id and last_metadata:
        music_prompt_file = _write_music_prompt(settings, last_stage_id, last_metadata)
        sync_stage_non_video_assets(settings, generation_config, last_stage_id)
        print(f"Music prompt saved: {music_prompt_file.name}")

    print("All generated assets still need to be imported into Adobe Premiere manually.")


def _music_prompt_text(metadata: ImageMetadata, stage_id: str) -> str:
    return "\n".join(
        [
            f"Stage: {stage_id}",
            "Music prompt for the full summary video:",
            f"Tone: {metadata.atmosphere_label}, emotional but restrained, emphasizing memory and continuity.",
            f"Source frame format: {metadata.format_description}",
            f"Palette cue: {metadata.palette_label}",
            "Dynamics: soft build-up, light pulse in the middle, warm decay at the end.",
            "Instrumentation: live strings, soft brass accents, subtle analog synth.",
        ]
    )


if __name__ == "__main__":
    try:
        main()
    except ConfigValidationError as exc:
        raise SystemExit(f"Config validation error: {exc}") from exc
