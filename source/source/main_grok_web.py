from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat

from api.grok_web import GrokWebAgent, GrokWebConfig
from config import ConfigValidationError, GenerationConfig, Settings, load_generation_config
from utils.project_delivery import sync_final_media_file, sync_stage_non_video_assets, sync_video_file

SUPPORTED_INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
PROMPT_NAME_RE = re.compile(r"^(?P<image_stem>.+)_\d{8}_\d{6}_v_prompt_(?P<index>\d+)$")
BACKGROUND_IDENTICAL_RMS_THRESHOLD = 8.0


AgentRunner = Callable[[GrokWebConfig], Path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a video in Chrome Grok from an input image and an output v_prompt file.")
    parser.add_argument("--image", "-i", type=Path, default=None, help="Input image path or file name from input/.")
    parser.add_argument("--prompt", "-p", type=Path, default=None, help="v_prompt file path or file name from output/.")
    parser.add_argument("--output-video", "-o", type=Path, default=None, help="Optional target MP4 path. Defaults to output/<stage>_video_<N>.mp4.")
    parser.add_argument("--config-file", type=Path, default=None, help="Optional generation config JSON.")
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile/grok-web"), help="Persistent Chrome profile for Grok Web.")
    parser.add_argument("--target-url", type=str, default="https://grok.com/imagine", help="Grok Web URL.")
    parser.add_argument("--chrome-exe", type=Path, default=None, help="Optional explicit Chrome executable path.")
    parser.add_argument("--chrome-debug-port", type=int, default=None, help="Optional Chrome remote debugging port for reusing an already opened Grok login window.")
    parser.add_argument("--result-timeout", type=float, default=600.0, help="How long to wait for the generated video, in seconds.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="How long to wait for Grok Web to open, in seconds.")
    parser.add_argument("--upload-timeout", type=float, default=180.0, help="How long to wait for image upload readiness before submit, in seconds.")
    parser.add_argument(
        "--generate-video",
        action="store_true",
        dest="generate_video",
        default=None,
        help="Generate video output in Grok.",
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
    parser.add_argument("--no-submit", action="store_true", help="Fill the form without submitting it.")
    return parser.parse_args()


def derive_image_stem(prompt_path: Path) -> str:
    match = PROMPT_NAME_RE.match(prompt_path.stem)
    if match:
        return match.group("image_stem")
    if "_v_prompt_" in prompt_path.stem:
        return prompt_path.stem.split("_v_prompt_", 1)[0]
    raise ValueError(f"Could not derive input image stem from prompt file: {prompt_path.name}")


def resolve_image_for_prompt(prompt_path: Path, settings: Settings) -> Path:
    image_stem = derive_image_stem(prompt_path)
    for suffix in sorted(SUPPORTED_INPUT_SUFFIXES):
        candidate = settings.input_dir / f"{image_stem}{suffix}"
        if candidate.exists():
            return candidate
    matches = sorted(
        path for path in settings.input_dir.iterdir() if path.is_file() and path.stem == image_stem and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
    )
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Input image for prompt was not found: {prompt_path.name}")


def resolve_image_path(image_arg: Path | None, settings: Settings, prompt_path: Path | None = None) -> Path:
    if image_arg is not None:
        if image_arg.exists():
            return image_arg
        candidate = settings.input_dir / image_arg
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Input image was not found: {image_arg}")

    if prompt_path is not None:
        return resolve_image_for_prompt(prompt_path, settings)

    images = sorted(path for path in settings.input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES)
    if len(images) == 1:
        return images[0]
    if not images:
        raise FileNotFoundError(f"No source images found in input/: {settings.input_dir}")
    raise ValueError("More than one input image is available. Use --image or --prompt to choose one.")


def resolve_prompt_path(prompt_arg: Path | None, image_path: Path, settings: Settings) -> Path:
    if prompt_arg is not None:
        if prompt_arg.exists():
            return prompt_arg
        candidate = settings.output_dir / prompt_arg
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"v_prompt file was not found: {prompt_arg}")

    matching = sorted(settings.output_dir.glob(f"*{image_path.stem}*_v_prompt_*.txt"))
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        return max(matching, key=lambda path: path.stat().st_mtime)

    prompts = sorted(settings.output_dir.glob("*_v_prompt_*.txt"))
    if len(prompts) == 1:
        return prompts[0]
    if not prompts:
        raise FileNotFoundError(f"No v_prompt files found in output/: {settings.output_dir}")
    raise ValueError("More than one v_prompt file is available. Use --prompt to choose one.")


def resolve_prompt_path_without_image(prompt_arg: Path | None, settings: Settings) -> Path:
    if prompt_arg is not None:
        if prompt_arg.exists():
            return prompt_arg
        candidate = settings.output_dir / prompt_arg
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"v_prompt file was not found: {prompt_arg}")

    prompts = sorted((path for path in settings.output_dir.glob("*_v_prompt_*.txt") if path.is_file()), key=lambda path: path.stat().st_mtime)
    if not prompts:
        raise FileNotFoundError(f"No v_prompt files found in output/: {settings.output_dir}")
    return prompts[-1]


def default_output_video_path(prompt_path: Path, settings: Settings) -> Path:
    stem = prompt_path.stem
    if "_v_prompt_" in stem:
        stem = stem.replace("_v_prompt_", "_video_")
    else:
        stem = f"{stem}_video"
    return settings.output_dir / f"{stem}.mp4"


def load_runtime_config(config_path: Path | None, settings: Settings) -> GenerationConfig:
    default_path = settings.project_root / "config.json"
    resolved = config_path or (default_path if default_path.exists() else None)
    return load_generation_config(resolved)


def _stage_id_from_prompt(prompt_path: Path) -> str:
    if "_v_prompt_" in prompt_path.stem:
        return prompt_path.stem.split("_v_prompt_", 1)[0]
    return prompt_path.stem


def _background_prompt_path(stage_id: str, settings: Settings) -> Path:
    return settings.output_dir / f"{stage_id}_bg_prompt.txt"


def _association_prompt_path(stage_id: str, settings: Settings) -> Path:
    return settings.output_dir / f"{stage_id}_assoc_bg_prompt.txt"


def _background_generation_prompt_path(stage_id: str, settings: Settings) -> Path:
    return _association_prompt_path(stage_id, settings)


def _background_image_path(stage_id: str, settings: Settings) -> Path:
    return settings.output_dir / f"{stage_id}_bg_image_16x9.png"


def _background_manifest_path(stage_id: str, settings: Settings) -> Path:
    return settings.output_dir / f"{stage_id}_api_pipeline_manifest.json"


def _scene_analysis_path(stage_id: str, settings: Settings) -> Path:
    return settings.output_dir / f"{stage_id}_scene_analysis.json"


def _background_image_is_near_identical(source_path: Path, generated_path: Path) -> tuple[bool, list[float]]:
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    with Image.open(source_path).convert("RGB") as source_image, Image.open(generated_path).convert("RGB") as generated_image:
        fitted_source = ImageOps.fit(source_image, generated_image.size, method=resampling)
        diff = ImageChops.difference(fitted_source, generated_image)
        stat = ImageStat.Stat(diff)
    rms = [float(value) for value in stat.rms]
    return max(rms) < BACKGROUND_IDENTICAL_RMS_THRESHOLD, rms


def _strengthen_associative_background_prompt(prompt_text: str) -> str:
    emphasis = (
        " Build a clearly visible realistic associative world from this descriptor. "
        "Use the source photo only as loose visual guidance for mood and composition. "
        "Do not return a near-identical copy of the source frame. "
        "Do not preserve the original people, table setting, or exact foreground objects literally. "
        "Make the associated environment dominant, photorealistic, and easy to recognize."
    )
    return f"{prompt_text.strip()}{emphasis}"


def _mark_background_manual_required(stage_id: str, settings: Settings, reason: str) -> None:
    manifest_path = _background_manifest_path(stage_id, settings)
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    artifacts = manifest.setdefault("artifacts", {})
    if isinstance(artifacts, dict):
        artifacts["bg_image"] = None
        artifacts["bg_image_status"] = "manual_required"
        artifacts["bg_image_reason"] = reason
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _delete_background_output(path: Path) -> None:
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            time.sleep(0.1 * (attempt + 1))
        if not path.exists():
            return


def _load_scene_analysis_payload(stage_id: str, settings: Settings) -> dict[str, object]:
    scene_path = _scene_analysis_path(stage_id, settings)
    if not scene_path.exists():
        return {}
    try:
        payload = json.loads(scene_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _association_descriptor(stage_id: str, settings: Settings) -> str:
    parts: list[str] = []
    payload = _load_scene_analysis_payload(stage_id, settings)
    for key in ("summary", "background", "shot_type", "main_action"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip().lower())
    mood = payload.get("mood")
    if isinstance(mood, list):
        parts.extend(str(item).strip().lower() for item in mood if str(item).strip())
    return " ".join(parts)


def _association_prompt_descriptor(stage_id: str, settings: Settings) -> str:
    association_prompt_path = _association_prompt_path(stage_id, settings)
    if not association_prompt_path.exists():
        return ""
    try:
        return association_prompt_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _infer_association_theme(stage_id: str, settings: Settings) -> str:
    vegetation_tokens = ("tree", "leaf", "garden", "park", "forest", "green", "flower", "grass", "сад", "дерев", "лист", "лес", "парк", "цвет", "трава")
    landscape_tokens = ("mountain", "sea", "beach", "sky", "horizon", "valley", "river", "landscape", "закат", "небо", "гор", "река", "море", "берег", "пейзаж")
    architecture_tokens = ("arch", "brick", "stone", "kitchen", "interior", "room", "window", "building", "corridor", "table", "стол", "арка", "кирпич", "камн", "кухн", "интерьер", "комнат", "окно", "дом")

    def _score(text: str) -> tuple[int, int, int]:
        return (
            sum(token in text for token in architecture_tokens),
            sum(token in text for token in vegetation_tokens),
            sum(token in text for token in landscape_tokens),
        )

    association_haystack = _association_prompt_descriptor(stage_id, settings)
    if association_haystack:
        architecture_score, vegetation_score, landscape_score = _score(association_haystack)
        if max(architecture_score, vegetation_score, landscape_score) > 0:
            if architecture_score >= max(vegetation_score, landscape_score):
                return "architecture"
            if vegetation_score >= landscape_score:
                return "vegetation"
            return "landscape"

    haystack = _association_descriptor(stage_id, settings)
    architecture_score, vegetation_score, landscape_score = _score(haystack)

    if architecture_score >= max(vegetation_score, landscape_score, 1):
        return "architecture"
    if vegetation_score >= max(landscape_score, 1):
        return "vegetation"
    if landscape_score >= 1:
        return "landscape"
    return "nature"


def _build_associative_overlay(source_frame: Image.Image, size: tuple[int, int], theme: str, mode: str, descriptor: str) -> Image.Image:
    width, height = size
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    photo = ImageOps.fit(source_frame, size, method=resampling)
    shifted = ImageChops.offset(photo, width // 7, -height // 12).filter(ImageFilter.GaussianBlur(radius=11))
    mirrored = ImageOps.mirror(photo).filter(ImageFilter.GaussianBlur(radius=15))
    diagonal = ImageChops.offset(photo, -width // 10, height // 14).filter(ImageFilter.GaussianBlur(radius=13))

    if theme == "architecture":
        primary = (136, 112, 92) if "brick" in descriptor or "stone" in descriptor else (122, 118, 112)
        secondary = (86, 74, 66) if mode == "dark cinematic background" else (214, 196, 182)
    elif theme == "vegetation":
        primary = (86, 132, 84) if mode == "dark cinematic background" else (156, 196, 132)
        secondary = (62, 96, 58) if mode == "dark cinematic background" else (218, 232, 188)
    elif theme == "landscape":
        primary = (94, 122, 162) if mode == "dark cinematic background" else (198, 214, 232)
        secondary = (66, 86, 124) if mode == "dark cinematic background" else (228, 226, 204)
    else:
        primary = (102, 116, 90) if mode == "dark cinematic background" else (196, 208, 186)
        secondary = (76, 88, 70) if mode == "dark cinematic background" else (228, 222, 198)

    associative = Image.blend(shifted, mirrored, 0.44)
    associative = Image.blend(associative, diagonal, 0.26)
    associative = Image.blend(associative, Image.new("RGB", size, primary), 0.34)
    associative = Image.blend(associative, Image.new("RGB", size, secondary), 0.18)
    associative = ImageEnhance.Contrast(associative).enhance(1.08 if mode == "dark cinematic background" else 1.04)
    associative = ImageEnhance.Color(associative).enhance(1.16 if theme in {"vegetation", "landscape"} else 1.10)
    associative = ImageEnhance.Brightness(associative).enhance(0.94 if mode == "dark cinematic background" else 1.06)
    return associative


def _select_background_local_fallback_mode(mean_brightness: float) -> str:
    return "light airy background" if mean_brightness < 118 else "dark cinematic background"


def _association_style_profile(descriptor: str, mean_brightness: float) -> dict[str, object]:
    text = descriptor.lower()
    light_tokens = ("light", "airy", "bright", "sunlit", "glow", "pastel", "soft daylight", "luminous")
    dark_tokens = ("dark", "cinematic", "moody", "shadow", "night", "deep", "dramatic", "noir")
    warm_tokens = ("warm", "gold", "golden", "amber", "terracotta", "sunset", "candle")
    cool_tokens = ("cool", "blue", "teal", "mist", "moon", "silver", "rain", "twilight")
    balanced_tokens = ("balanced", "natural", "subtle", "gentle", "realistic", "soft blend")
    vivid_tokens = ("lush", "rich", "vivid", "saturated", "bold")
    blur_tokens = ("mist", "fog", "haze", "soft", "blurred", "dreamy")

    mode = _select_background_local_fallback_mode(mean_brightness)
    if any(token in text for token in light_tokens) and not any(token in text for token in dark_tokens):
        mode = "light airy background"
    elif any(token in text for token in dark_tokens) and not any(token in text for token in light_tokens):
        mode = "dark cinematic background"

    profile: dict[str, object] = {
        "mode": mode,
        "zoom_factor": 1.82,
        "blur_radius": 30,
        "overlay_blend": 0.66 if mode == "dark cinematic background" else 0.60,
        "tint_blend": 0.54 if mode == "dark cinematic background" else 0.50,
        "brightness_factor": 0.52 if mode == "dark cinematic background" else 1.42,
        "contrast_factor": 0.58 if mode == "dark cinematic background" else 0.50,
        "color_factor": 0.20 if mode == "dark cinematic background" else 0.24,
        "tint_color": (58, 86, 144) if mode == "dark cinematic background" else (244, 228, 210),
    }

    if any(token in text for token in warm_tokens):
        profile["tint_color"] = (128, 88, 54) if mode == "dark cinematic background" else (242, 214, 182)
        profile["color_factor"] = float(profile["color_factor"]) + 0.08
    elif any(token in text for token in cool_tokens):
        profile["tint_color"] = (56, 92, 146) if mode == "dark cinematic background" else (206, 222, 242)
        profile["color_factor"] = float(profile["color_factor"]) + 0.04

    if any(token in text for token in blur_tokens):
        profile["blur_radius"] = int(profile["blur_radius"]) + 6
        profile["contrast_factor"] = max(0.34, float(profile["contrast_factor"]) - 0.08)

    if any(token in text for token in vivid_tokens):
        profile["overlay_blend"] = min(0.76, float(profile["overlay_blend"]) + 0.04)
        profile["color_factor"] = float(profile["color_factor"]) + 0.08

    if any(token in text for token in balanced_tokens):
        profile["overlay_blend"] = max(0.52, float(profile["overlay_blend"]) - 0.05)
        profile["tint_blend"] = max(0.42, float(profile["tint_blend"]) - 0.04)

    return profile


def _write_background_local_fallback(source_path: Path, output_path: Path, target_size: tuple[int, int], stage_id: str, settings: Settings) -> str:
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    transform_resampling = getattr(Image, "Resampling", Image).BICUBIC
    with Image.open(source_path).convert("RGB") as source_image:
        association_prompt_descriptor = _association_prompt_descriptor(stage_id, settings)
        association_descriptor = association_prompt_descriptor or _association_descriptor(stage_id, settings)
        initial_stat = ImageStat.Stat(source_image)
        mean_brightness = sum(initial_stat.mean) / len(initial_stat.mean)
        style_profile = _association_style_profile(association_descriptor, mean_brightness)
        mode = str(style_profile["mode"])
        zoom_factor = float(style_profile["zoom_factor"])
        zoomed_size = (
            max(target_size[0], int(target_size[0] * zoom_factor)),
            max(target_size[1], int(target_size[1] * zoom_factor)),
        )
        zoomed = ImageOps.fit(source_image, zoomed_size, method=resampling)
        distorted = zoomed.transform(
            zoomed.size,
            Image.Transform.AFFINE,
            (
                1.0,
                0.11,
                -zoomed.size[0] * 0.08,
                -0.05,
                1.0,
                zoomed.size[1] * 0.06,
            ),
            resample=transform_resampling,
        )
        rotated = distorted.rotate(
            -3.2,
            resample=transform_resampling,
            expand=True,
            fillcolor=(32, 24, 20),
        )
        fitted = ImageOps.fit(rotated, target_size, method=resampling)
        blurred = fitted.filter(ImageFilter.GaussianBlur(radius=int(style_profile["blur_radius"])))
        association_theme = _infer_association_theme(stage_id, settings)
        if mode == "light airy background":
            adjusted = ImageEnhance.Brightness(blurred).enhance(float(style_profile["brightness_factor"]))
            adjusted = ImageEnhance.Contrast(adjusted).enhance(float(style_profile["contrast_factor"]))
            adjusted = ImageEnhance.Color(adjusted).enhance(float(style_profile["color_factor"]))
            tint = Image.new("RGB", target_size, tuple(style_profile["tint_color"]))
            graded = Image.blend(adjusted, tint, float(style_profile["tint_blend"]))
        else:
            adjusted = ImageEnhance.Brightness(blurred).enhance(float(style_profile["brightness_factor"]))
            adjusted = ImageEnhance.Contrast(adjusted).enhance(float(style_profile["contrast_factor"]))
            adjusted = ImageEnhance.Color(adjusted).enhance(float(style_profile["color_factor"]))
            tint = Image.new("RGB", target_size, tuple(style_profile["tint_color"]))
            graded = Image.blend(adjusted, tint, float(style_profile["tint_blend"]))
        overlay = _build_associative_overlay(source_image, target_size, association_theme, mode, association_descriptor)
        composite = Image.blend(graded, overlay, float(style_profile["overlay_blend"]))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        composite.save(output_path)
        return f"{mode} with visible {association_theme} association"


def _mark_background_local_fallback(stage_id: str, settings: Settings, reason: str, background_path: Path) -> None:
    manifest_path = _background_manifest_path(stage_id, settings)
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    artifacts = manifest.setdefault("artifacts", {})
    if isinstance(artifacts, dict):
        artifacts["bg_image"] = str(background_path)
        artifacts["bg_image_status"] = "local_fallback"
        artifacts["bg_image_reason"] = reason
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def run_generation(args: argparse.Namespace, settings: Settings | None = None, runner: AgentRunner | None = None) -> Path:
    settings = settings or Settings()
    settings.ensure_output()
    generation_config = load_runtime_config(getattr(args, "config_file", None), settings).override(
        generate_video=getattr(args, "generate_video", None),
        generate_source_background=getattr(args, "generate_source_background", None),
        save_grok_debug_artifacts=getattr(args, "save_grok_debug_artifacts", None),
    )
    if not generation_config.generate_video and not generation_config.generate_source_background:
        raise ValueError("Both video generation and source-background generation are disabled. Nothing to generate.")

    prompt_path = resolve_prompt_path_without_image(args.prompt, settings) if args.image is None else None
    image_path = resolve_image_path(args.image, settings, prompt_path=prompt_path)
    prompt_path = prompt_path or resolve_prompt_path(args.prompt, image_path, settings)
    output_video = args.output_video if args.output_video is not None else default_output_video_path(prompt_path, settings)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    stage_id = _stage_id_from_prompt(prompt_path)

    config = GrokWebConfig(
        prompt_text=prompt_text,
        image_path=image_path,
        output_path=output_video,
        profile_dir=args.profile_dir,
        target_url=args.target_url,
        executable_path=args.chrome_exe,
        debug_port=getattr(args, "chrome_debug_port", None),
        launch_timeout_ms=int(args.launch_timeout * 1000),
        upload_timeout_ms=int(args.upload_timeout * 1000),
        result_timeout_ms=int(args.result_timeout * 1000),
        submit=not args.no_submit,
        save_debug_artifacts=generation_config.save_grok_debug_artifacts,
    )
    agent_runner = runner or (lambda cfg: GrokWebAgent(cfg).run())
    background_result: Path | None = None

    if generation_config.generate_source_background and not args.no_submit:
        background_prompt_path = _background_generation_prompt_path(stage_id, settings)
        if not background_prompt_path.exists():
            raise FileNotFoundError(
                f"Associative background prompt file was not found for stage '{stage_id}': {background_prompt_path}"
            )
        background_output = _background_image_path(stage_id, settings)
        if not background_output.exists():
            background_prompt_text = background_prompt_path.read_text(encoding="utf-8")
            background_config = GrokWebConfig(
                prompt_text=background_prompt_text,
                image_path=image_path,
                output_path=background_output,
                profile_dir=args.profile_dir,
                target_url=args.target_url,
                executable_path=args.chrome_exe,
                debug_port=getattr(args, "chrome_debug_port", None),
                launch_timeout_ms=int(args.launch_timeout * 1000),
                upload_timeout_ms=int(args.upload_timeout * 1000),
                result_timeout_ms=int(args.result_timeout * 1000),
                submit=True,
                generation_mode="image",
                aspect_ratio="16:9",
                orientation="horizontal",
                save_debug_artifacts=generation_config.save_grok_debug_artifacts,
            )
            background_result = agent_runner(background_config)
            if not background_result.exists():
                raise RuntimeError(f"Grok background run finished without saving the image file: {background_result}")
            print(f"Grok background image saved: {background_prompt_path.name} -> {background_result.name}", flush=True)
            sync_final_media_file(settings, generation_config, background_result)
            sync_stage_non_video_assets(settings, generation_config, stage_id, extra_files=[background_prompt_path, background_result])

    if not generation_config.generate_video:
        if background_result is None:
            raise RuntimeError("Video generation is disabled and no background image was created.")
        sync_stage_non_video_assets(settings, generation_config, stage_id, extra_files=[prompt_path])
        return background_result

    result_path = agent_runner(config)

    sync_stage_non_video_assets(settings, generation_config, stage_id, extra_files=[prompt_path])
    if result_path.exists():
        sync_video_file(settings, generation_config, result_path)
    return result_path


def main() -> None:
    args = parse_args()
    output_path = run_generation(args)
    if not output_path.exists():
        raise RuntimeError(f"Grok run finished without saving the expected output file: {output_path}")
    if args.no_submit:
        print(f"Grok form prepared. Planned output path: {output_path}")
    else:
        output_kind = "background image" if output_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "video"
        print(f"Grok {output_kind} saved to: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except ConfigValidationError as exc:
        raise SystemExit(f"Config validation error: {exc}") from exc
