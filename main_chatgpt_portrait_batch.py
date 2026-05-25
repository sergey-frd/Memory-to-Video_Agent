from __future__ import annotations

import argparse
import ctypes
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from api.openai_image import edit_image_with_openai
from api.chatgpt_web import ChatGPTWebConfig, ChatGPTWebSessionRunner
from config import GenerationConfig, Settings, load_generation_config
from utils.project_delivery import sync_final_output_file
from utils.image_analysis import analyze_image

SUPPORTED_INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DEFAULT_CONFIG_NAME = "chatgpt_portrait_config.json"
DEFAULT_OUTPUT_SUBDIR = "chatgpt_portraits"
DEFAULT_PAIR_INPUT_SUBDIR = "input_pair"
DEFAULT_PAIR_OUTPUT_SUBDIR = "pair"
CHATGPT_TARGET_URL = "https://chatgpt.com/"
DEFAULT_CHATGPT_TAB_TITLE_RE = ".*ChatGPT.*"
DEFAULT_PROMPT_TEMPLATE = "\n".join(
    [
        "Generate a new portrait image based on the attached source image.",
        "Style: {style}.",
        "Preserve the person's identity, facial structure, approximate age, expression, and natural features.",
        "Create one finished portrait, not a collage. Do not add text, captions, watermarks, frames, or extra people.",
    ]
)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_position() -> Optional[tuple[int, int]]:
    point = _POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return int(point.x), int(point.y)


class PortraitConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PortraitStyle:
    name: str
    prompt: Optional[str] = None
    slug: Optional[str] = None


@dataclass(frozen=True)
class PortraitBatchConfig:
    styles: list[PortraitStyle]
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    output_dir: Optional[Path] = None
    response_text_dir: Optional[Path] = None
    save_response_text: bool = False
    new_chat_per_job: bool = True


@dataclass(frozen=True)
class PortraitJob:
    image_path: Path
    style: PortraitStyle
    prompt_text: str
    output_path: Path
    response_text_path: Optional[Path]
    image_paths: tuple[Path, ...] = ()
    source_label: Optional[str] = None
    input_image_paths: tuple[Path, ...] = ()

    @property
    def source_image_paths(self) -> tuple[Path, ...]:
        return self.image_paths or (self.image_path,)

    @property
    def original_input_image_paths(self) -> tuple[Path, ...]:
        return self.input_image_paths or self.source_image_paths

    @property
    def display_name(self) -> str:
        return self.source_label or self.image_path.name

    @property
    def is_multi_source(self) -> bool:
        return len(self.source_image_paths) > 1


PortraitRunner = Callable[[ChatGPTWebConfig], Optional[Path]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate styled portraits for all images from input/."
    )
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory with source images. Defaults to input/.")
    parser.add_argument(
        "--input-pair-dir",
        type=Path,
        default=None,
        help=f"Directory with pair subfolders. Pair launchers use {DEFAULT_PAIR_INPUT_SUBDIR}/ by default.",
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for generated portraits.")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help=f"Portrait config JSON. Defaults to {DEFAULT_CONFIG_NAME}.",
    )
    parser.add_argument(
        "--delivery-config-file",
        type=Path,
        default=None,
        help="Optional user delivery config JSON with final_output_dir for copied portrait/image outputs.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".browser-profile/chatgpt-web"),
        help="Persistent Chrome profile for the selected web service.",
    )
    parser.add_argument("--target-url", type=str, default=CHATGPT_TARGET_URL, help="Web URL for the selected service.")
    parser.add_argument("--chrome-exe", type=Path, default=None, help="Optional explicit Chrome executable path.")
    parser.add_argument(
        "--chrome-debug-port",
        type=int,
        default=None,
        help="Connect to an already opened Chrome session instead of launching a new automation window.",
    )
    parser.add_argument(
        "--backend",
        choices=("web", "desktop", "gemini-desktop", "gemini", "grok", "grok-web", "api", "local"),
        default="web",
        help="Use ChatGPT Web automation, Grok Web automation, an already-open ChatGPT or Gemini desktop Chrome window, the official OpenAI Images API, or local stylization.",
    )
    parser.add_argument(
        "--api-model",
        type=str,
        default=None,
        help="OpenAI image edit model for --backend api, for example gpt-image-1.5 or dall-e-2.",
    )
    parser.add_argument("--result-timeout", type=float, default=300.0, help="Seconds to wait for each portrait.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="Seconds to wait for the web service to open.")
    parser.add_argument("--upload-timeout", type=float, default=180.0, help="Seconds to wait for source-image upload readiness in Grok.")
    parser.add_argument("--grok-aspect-ratio", type=str, default=None, help="Optional Grok image aspect ratio, for example 1:1 or 16:9.")
    parser.add_argument("--grok-orientation", type=str, default=None, help="Optional Grok image orientation, for example horizontal.")
    parser.add_argument("--save-grok-debug-artifacts", action="store_true", help="Keep Grok screenshot/HTML/candidate debug artifacts when image saving fails.")
    parser.add_argument("--desktop-window-title-re", type=str, default=".*Google Chrome.*", help="Regex for the existing browser window.")
    parser.add_argument("--desktop-browser-tab-title-re", type=str, default=DEFAULT_CHATGPT_TAB_TITLE_RE, help="Regex for the existing service browser tab.")
    parser.add_argument("--desktop-dialog-timeout", type=float, default=20.0, help="Seconds to wait for the Windows open-file dialog.")
    parser.add_argument("--desktop-new-chat-timeout", type=float, default=15.0, help="Seconds to wait while opening a new service chat.")
    parser.add_argument("--desktop-active-window", action="store_true", help="Use the currently active window instead of searching Chrome windows/tabs.")
    parser.add_argument("--desktop-reuse-selected-window", action="store_true", help="Select the desktop service window once, then reuse its window handle for the remaining jobs.")
    parser.add_argument("--desktop-prefer-single-tab-window", action="store_true", help="Prefer a service Chrome window with exactly one visible tab when several service windows are open.")
    parser.add_argument("--desktop-require-single-tab-window", action="store_true", help="Only use a service Chrome window with exactly one visible tab; fail fast if the selected window has extra tabs.")
    parser.add_argument("--desktop-new-chat", action="store_true", help="Try to open a new service chat before every desktop job.")
    parser.add_argument("--desktop-no-home-navigation", action="store_true", help="Use only the visible New chat control between jobs; do not navigate the browser address bar to the service home URL.")
    parser.add_argument("--desktop-clipboard-attach", action="store_true", help="Attach images by pasting the file from Windows clipboard into the active service composer.")
    parser.add_argument("--desktop-capture-result", action="store_true", help="Capture a generated image from the desktop UI after submitting.")
    parser.add_argument("--desktop-save-context-menu", action="store_true", help="Try to save the generated image through the browser image context menu.")
    parser.add_argument("--desktop-reactivate-delay", type=float, default=0.0, help="Seconds to wait before each desktop job so you can activate the service composer.")
    parser.add_argument("--desktop-send-cursor-delay", type=float, default=0.0, help="Seconds to wait after pasting so you can move the mouse over the active send arrow.")
    parser.add_argument("--desktop-click-composer", action="store_true", help="Click an estimated service composer position before pasting. Off by default for active-window mode.")
    parser.add_argument("--desktop-post-attach-delay", type=float, default=3.0, help="Seconds to wait after pasting the source image into the service composer.")
    parser.add_argument("--desktop-min-result-wait", type=float, default=90.0, help="Minimum seconds to wait after submitting before auto-saving a result image.")
    parser.add_argument("--desktop-result-stable-wait", type=float, default=8.0, help="Seconds a result image must stay stable before auto-saving.")
    parser.add_argument("--desktop-wait-mouse-idle-sec", type=float, default=0.0, help="Require this many seconds of no mouse movement before desktop clicks/keystrokes.")
    parser.add_argument("--desktop-mouse-idle-timeout-sec", type=float, default=60.0, help="Maximum seconds to wait for the mouse to become idle before a desktop action.")
    parser.add_argument("--pause-between-jobs", action="store_true", help="After each submitted job, wait for Enter before continuing.")
    parser.add_argument("--desktop-verbose", action="store_true", help="Print detailed desktop automation progress.")
    parser.add_argument(
        "--manual-verification-timeout",
        type=float,
        default=600.0,
        help="Seconds to wait while you manually complete service sign-in or human verification.",
    )
    parser.add_argument("--no-submit", action="store_true", help="Fill each service request without submitting it.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip portrait files that already exist.")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Log a failed job and continue with the next image/style pair.",
    )
    parser.add_argument(
        "--save-response-text",
        action="store_true",
        dest="save_response_text",
        default=None,
        help="Save visible service response text next to portrait outputs.",
    )
    parser.add_argument(
        "--skip-response-text",
        action="store_false",
        dest="save_response_text",
        help="Do not save service response text even if enabled in config.",
    )
    return parser.parse_args()


def _config_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    duplicates: list[str] = []
    for key, value in pairs:
        if key in data:
            duplicates.append(key)
        data[key] = value
    if duplicates:
        raise PortraitConfigError(f"Duplicate portrait config key(s): {', '.join(sorted(set(duplicates)))}")
    return data


def load_portrait_config(path: Path) -> PortraitBatchConfig:
    if not path.exists():
        raise FileNotFoundError(f"Portrait config was not found: {path}")
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle, object_pairs_hook=_config_object_pairs_hook)
    if not isinstance(data, dict):
        raise PortraitConfigError("Portrait config root must be a JSON object.")

    allowed = {
        "portrait_styles",
        "pair_styles",
        "styles",
        "prompt_template",
        "output_dir",
        "response_text_dir",
        "save_response_text",
        "new_chat_per_job",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise PortraitConfigError(f"Unknown portrait config key(s): {', '.join(unknown)}")
    style_keys = [key for key in ("portrait_styles", "pair_styles", "styles") if key in data]
    if len(style_keys) > 1:
        raise PortraitConfigError("Use only one of 'portrait_styles', 'pair_styles', or 'styles'.")

    styles_payload = data.get("portrait_styles", data.get("pair_styles", data.get("styles")))
    styles = _parse_styles(styles_payload)
    prompt_template = _non_empty_string(data.get("prompt_template", DEFAULT_PROMPT_TEMPLATE), "prompt_template")
    output_dir = _optional_path(data.get("output_dir"), "output_dir")
    response_text_dir = _optional_path(data.get("response_text_dir"), "response_text_dir")
    save_response_text = _optional_bool(data.get("save_response_text", False), "save_response_text")
    new_chat_per_job = _optional_bool(data.get("new_chat_per_job", True), "new_chat_per_job")
    return PortraitBatchConfig(
        styles=styles,
        prompt_template=prompt_template,
        output_dir=output_dir,
        response_text_dir=response_text_dir,
        save_response_text=save_response_text,
        new_chat_per_job=new_chat_per_job,
    )


def _parse_styles(payload: Any) -> list[PortraitStyle]:
    if payload is None:
        raise PortraitConfigError("Portrait config must contain a non-empty 'portrait_styles' list.")
    if isinstance(payload, str):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise PortraitConfigError("Portrait config 'portrait_styles' must be a non-empty list.")

    styles: list[PortraitStyle] = []
    for index, item in enumerate(payload, start=1):
        if isinstance(item, str):
            styles.append(PortraitStyle(name=_non_empty_string(item, f"portrait_styles[{index}]")))
            continue
        if isinstance(item, dict):
            unknown = sorted(set(item) - {"name", "style", "prompt", "slug"})
            if unknown:
                raise PortraitConfigError(
                    f"Unknown key(s) in portrait_styles[{index}]: {', '.join(unknown)}"
                )
            name = _non_empty_string(item.get("name", item.get("style")), f"portrait_styles[{index}].name")
            prompt = item.get("prompt")
            slug = item.get("slug")
            styles.append(
                PortraitStyle(
                    name=name,
                    prompt=_non_empty_string(prompt, f"portrait_styles[{index}].prompt") if prompt is not None else None,
                    slug=_non_empty_string(slug, f"portrait_styles[{index}].slug") if slug is not None else None,
                )
            )
            continue
        raise PortraitConfigError(f"portrait_styles[{index}] must be a string or object.")
    return styles


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortraitConfigError(f"Portrait config '{field_name}' must be a non-empty string.")
    return value.strip()


def _optional_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PortraitConfigError(f"Portrait config '{field_name}' must be true or false.")
    return value


def _optional_path(value: Any, field_name: str) -> Optional[Path]:
    if value is None:
        return None
    text = _non_empty_string(value, field_name)
    return Path(text)


def list_input_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory was not found: {input_dir}")
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
    )


def build_portrait_jobs(
    images: list[Path],
    portrait_config: PortraitBatchConfig,
    output_dir: Path,
    response_text_dir: Optional[Path] = None,
) -> list[PortraitJob]:
    style_slugs = _style_slugs(portrait_config.styles)
    jobs: list[PortraitJob] = []
    for image_path in images:
        for style, style_slug in zip(portrait_config.styles, style_slugs):
            prompt_text = _render_prompt(
                style.prompt or portrait_config.prompt_template,
                style=style.name,
                image_path=image_path,
            )
            output_path = output_dir / f"{image_path.stem}_{style_slug}.png"
            response_path = None
            if response_text_dir is not None:
                response_path = response_text_dir / f"{image_path.stem}_{style_slug}_response.txt"
            jobs.append(
                PortraitJob(
                    image_path=image_path,
                    style=style,
                    prompt_text=prompt_text,
                    output_path=output_path,
                    response_text_path=response_path,
                )
            )
    return jobs


def list_input_image_pairs(input_pair_dir: Path) -> list[tuple[str, tuple[Path, Path]]]:
    if not input_pair_dir.exists():
        raise FileNotFoundError(f"Pair input directory was not found: {input_pair_dir}")
    if not input_pair_dir.is_dir():
        raise NotADirectoryError(f"Pair input path is not a directory: {input_pair_dir}")

    pairs: list[tuple[str, tuple[Path, Path]]] = []
    skipped: list[str] = []
    for pair_dir in sorted(path for path in input_pair_dir.iterdir() if path.is_dir()):
        images = sorted(
            path
            for path in pair_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
        )
        if len(images) < 2:
            skipped.append(f"{pair_dir.name} ({len(images)} image(s))")
            continue
        if len(images) > 2:
            print(
                f"Pair input folder {pair_dir} contains {len(images)} images; using the first two sorted files.",
                flush=True,
            )
        pairs.append((pair_dir.name, (images[0], images[1])))
    if skipped:
        print(f"Skipped pair folders without two images: {', '.join(skipped)}", flush=True)
    return pairs


def build_pair_jobs(
    pairs: list[tuple[str, tuple[Path, Path]]],
    pair_config: PortraitBatchConfig,
    output_dir: Path,
    response_text_dir: Optional[Path] = None,
    run_timestamp: Optional[str] = None,
    reference_image_paths: Optional[dict[str, Path]] = None,
) -> list[PortraitJob]:
    style_slugs = _style_slugs(pair_config.styles)
    timestamp = run_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    include_style_slug = len(style_slugs) > 1
    jobs: list[PortraitJob] = []
    for pair_name, image_paths in pairs:
        attached_image_paths: tuple[Path, ...] = (
            (reference_image_paths[pair_name],)
            if reference_image_paths is not None and pair_name in reference_image_paths
            else image_paths
        )
        for style, style_slug in zip(pair_config.styles, style_slugs):
            prompt_text = _render_pair_prompt(
                style.prompt or pair_config.prompt_template,
                style=style.name,
                pair_name=pair_name,
                image_paths=image_paths,
            )
            pair_slug = _slugify(pair_name) or pair_name
            style_part = f"_{style_slug}" if include_style_slug else ""
            output_path = output_dir / f"{pair_slug}_art_pair{style_part}_{timestamp}.png"
            response_path = None
            if response_text_dir is not None:
                response_path = response_text_dir / f"{output_path.stem}_response.txt"
            jobs.append(
                PortraitJob(
                    image_path=attached_image_paths[0],
                    image_paths=attached_image_paths,
                    input_image_paths=image_paths,
                    source_label=pair_name,
                    style=style,
                    prompt_text=prompt_text,
                    output_path=output_path,
                    response_text_path=response_path,
                )
            )
    return jobs


def create_pair_reference_images(
    pairs: list[tuple[str, tuple[Path, Path]]],
    reference_dir: Path,
    run_timestamp: str,
) -> dict[str, Path]:
    reference_dir.mkdir(parents=True, exist_ok=True)
    references: dict[str, Path] = {}
    for pair_name, image_paths in pairs:
        pair_slug = _slugify(pair_name) or pair_name
        reference_path = reference_dir / f"{pair_slug}_pair_reference_{run_timestamp}.jpg"
        _create_pair_reference_image(image_paths, reference_path)
        references[pair_name] = reference_path
    return references


def _create_pair_reference_image(image_paths: tuple[Path, Path], output_path: Path) -> Path:
    slot_width = 900
    slot_height = 1200
    gutter = 80
    canvas = Image.new("RGB", (slot_width * 2 + gutter, slot_height), (255, 255, 255))
    for index, image_path in enumerate(image_paths):
        panel = _pair_reference_panel(image_path, slot_width, slot_height)
        x = index * (slot_width + gutter)
        canvas.paste(panel, (x, 0))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=95, subsampling=0)
    return output_path


def _pair_reference_panel(image_path: Path, slot_width: int, slot_height: int) -> Image.Image:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((slot_width, slot_height), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (slot_width, slot_height), (248, 248, 248))
    x = (slot_width - image.width) // 2
    y = (slot_height - image.height) // 2
    panel.paste(image, (x, y))
    return panel


def _render_prompt(template: str, *, style: str, image_path: Path) -> str:
    try:
        return template.format(style=style, image_name=image_path.name, image_stem=image_path.stem)
    except KeyError as exc:
        raise PortraitConfigError(f"Unknown prompt template placeholder: {exc}") from exc


def _render_pair_prompt(
    template: str,
    *,
    style: str,
    pair_name: str,
    image_paths: tuple[Path, Path],
) -> str:
    try:
        return template.format(
            style=style,
            pair_name=pair_name,
            image_1_name=image_paths[0].name,
            image_1_stem=image_paths[0].stem,
            image_2_name=image_paths[1].name,
            image_2_stem=image_paths[1].stem,
        )
    except KeyError as exc:
        raise PortraitConfigError(f"Unknown prompt template placeholder: {exc}") from exc


def _style_slugs(styles: list[PortraitStyle]) -> list[str]:
    used: dict[str, int] = {}
    slugs: list[str] = []
    for index, style in enumerate(styles, start=1):
        base = _slugify(style.slug or style.name) or f"style_{index:02d}"
        count = used.get(base, 0) + 1
        used[base] = count
        slugs.append(base if count == 1 else f"{base}_{count:02d}")
    return slugs


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]+", normalized.lower())
    return "_".join(words[:8])


def _resolve_under_project(path: Optional[Path], settings: Settings) -> Optional[Path]:
    if path is None:
        return None
    return path if path.is_absolute() else settings.project_root / path


def _is_gemini_backend(backend: str) -> bool:
    return backend in {"gemini-desktop", "gemini"}


def _is_grok_backend(backend: str) -> bool:
    return backend in {"grok", "grok-web"}


def _service_output_dir_for_chatgpt_dir(output_dir: Path, settings: Settings, service_prefix: str) -> Path:
    name = output_dir.name
    name_cf = name.casefold()
    wanted_prefix = f"{service_prefix}_"
    if name_cf.startswith(wanted_prefix):
        return output_dir
    if name_cf.startswith("chatgpt_"):
        return output_dir.with_name(f"{wanted_prefix}{name[len('chatgpt_'):]}")

    try:
        relative = output_dir.relative_to(settings.output_dir)
    except ValueError:
        return output_dir

    if not relative.parts:
        return output_dir

    first = relative.parts[0]
    first_cf = first.casefold()
    if first_cf.startswith(wanted_prefix):
        return output_dir
    if first_cf.startswith("chatgpt_"):
        first = f"{wanted_prefix}{first[len('chatgpt_'):]}"
    else:
        first = f"{wanted_prefix}{first}"
    return settings.output_dir.joinpath(first, *relative.parts[1:])


def _gemini_output_dir_for_chatgpt_dir(output_dir: Path, settings: Settings) -> Path:
    return _service_output_dir_for_chatgpt_dir(output_dir, settings, "gemini")


def _grok_output_dir_for_chatgpt_dir(output_dir: Path, settings: Settings) -> Path:
    return _service_output_dir_for_chatgpt_dir(output_dir, settings, "grok")


def _output_dir_for_backend(output_dir: Path, backend: str, settings: Settings) -> Path:
    if _is_gemini_backend(backend):
        return _gemini_output_dir_for_chatgpt_dir(output_dir, settings)
    if _is_grok_backend(backend):
        return _grok_output_dir_for_chatgpt_dir(output_dir, settings)
    return output_dir


def _profile_dir_for_backend(profile_dir: Path, backend: str) -> Path:
    if _is_grok_backend(backend) and profile_dir == Path(".browser-profile/chatgpt-web"):
        return Path(".browser-profile/grok-web")
    return profile_dir


def _load_delivery_config(args: argparse.Namespace, settings: Settings) -> GenerationConfig | None:
    config_path = getattr(args, "delivery_config_file", None)
    if config_path is None:
        return None
    if not config_path.is_absolute() and not config_path.exists():
        config_path = settings.project_root / config_path
    if not config_path.exists():
        raise FileNotFoundError(f"Delivery config was not found: {config_path}")
    return load_generation_config(config_path)


def _sync_final_portrait_output(
    settings: Settings,
    delivery_config: GenerationConfig | None,
    output_path: Path,
) -> Optional[Path]:
    if delivery_config is None:
        return None
    delivered_path = sync_final_output_file(settings, delivery_config, output_path)
    if not output_path.exists():
        raise FileNotFoundError(f"Generated output is missing and cannot be delivered: {output_path}")
    if delivered_path == output_path:
        return delivered_path
    if not delivered_path.exists():
        raise FileNotFoundError(f"Delivery target was not created: {delivered_path}")
    source_size = output_path.stat().st_size
    delivered_size = delivered_path.stat().st_size
    if delivered_size != source_size:
        raise OSError(
            "Delivery target size mismatch: "
            f"{delivered_path} has {delivered_size} bytes, expected {source_size} bytes."
        )
    return delivered_path


def _existing_output_for_skip(job: PortraitJob) -> Optional[Path]:
    if job.output_path.exists():
        return job.output_path
    if job.source_label is None:
        return None
    pair_slug = _slugify(job.source_label) or job.source_label
    candidates = sorted(job.output_path.parent.glob(f"{pair_slug}_art_pair*.png"))
    if not candidates:
        return None
    try:
        source_mtime = max(path.stat().st_mtime for path in job.original_input_image_paths if path.exists())
    except ValueError:
        source_mtime = 0.0
    valid_candidates = [
        path
        for path in candidates
        if path.is_file() and path.stat().st_mtime >= source_mtime
    ]
    if not valid_candidates:
        return None
    return max(valid_candidates, key=lambda path: path.stat().st_mtime)


def run_batch(
    args: argparse.Namespace,
    settings: Settings | None = None,
    runner: PortraitRunner | None = None,
) -> list[Path]:
    settings = settings or Settings()
    settings.ensure_output()

    config_path = args.config_file or (settings.project_root / DEFAULT_CONFIG_NAME)
    portrait_config = load_portrait_config(config_path)
    delivery_config = _load_delivery_config(args, settings)
    backend = getattr(args, "backend", "web")
    input_pair_dir = getattr(args, "input_pair_dir", None)
    pair_mode = input_pair_dir is not None
    if pair_mode and backend not in {"desktop", "gemini-desktop", "gemini"}:
        raise PortraitConfigError(
            "Pair generation currently requires a desktop backend because two source images must be attached."
        )
    input_dir = args.input_dir or settings.input_dir
    cli_output_dir = _resolve_under_project(args.output_dir, settings)
    output_dir = cli_output_dir
    if output_dir is None:
        output_dir = _resolve_under_project(portrait_config.output_dir, settings)
        if output_dir is not None:
            output_dir = _output_dir_for_backend(output_dir, backend, settings)
    if output_dir is None:
        output_dir = settings.output_dir / (DEFAULT_PAIR_OUTPUT_SUBDIR if pair_mode else DEFAULT_OUTPUT_SUBDIR)
        output_dir = _output_dir_for_backend(output_dir, backend, settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    save_response_text = portrait_config.save_response_text
    cli_save_response_text = getattr(args, "save_response_text", None)
    if cli_save_response_text is not None:
        save_response_text = cli_save_response_text
    response_text_dir = None
    if save_response_text:
        response_text_dir = _resolve_under_project(portrait_config.response_text_dir, settings) or output_dir
        if response_text_dir != output_dir and cli_output_dir is None:
            response_text_dir = _output_dir_for_backend(response_text_dir, backend, settings)
        response_text_dir.mkdir(parents=True, exist_ok=True)

    if pair_mode:
        resolved_pair_dir = _resolve_under_project(input_pair_dir, settings) or (settings.project_root / DEFAULT_PAIR_INPUT_SUBDIR)
        pairs = list_input_image_pairs(resolved_pair_dir)
        if not pairs:
            raise FileNotFoundError(f"No pair folders with two source images found in: {resolved_pair_dir}")
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        reference_image_paths = create_pair_reference_images(
            pairs,
            output_dir / "_pair_references",
            run_timestamp,
        )
        jobs = build_pair_jobs(
            pairs,
            portrait_config,
            output_dir,
            response_text_dir,
            run_timestamp=run_timestamp,
            reference_image_paths=reference_image_paths,
        )
    else:
        images = list_input_images(input_dir)
        if not images:
            raise FileNotFoundError(f"No source images found in: {input_dir}")
        jobs = build_portrait_jobs(images, portrait_config, output_dir, response_text_dir)
    if backend in {"desktop", "gemini-desktop", "gemini"}:
        return _run_desktop_jobs(args, jobs, portrait_config, settings, delivery_config)
    if _is_grok_backend(backend):
        return _run_grok_jobs(args, jobs, settings, delivery_config)
    if backend == "local":
        return _run_local_jobs(args, jobs, settings, delivery_config)
    if backend == "api":
        return _run_api_jobs(args, jobs, settings, delivery_config)

    session_runner: ChatGPTWebSessionRunner | None = None
    if runner is None:
        session_runner = ChatGPTWebSessionRunner()
        resolved_runner = session_runner.run
    else:
        resolved_runner = runner

    outputs: list[Path] = []
    try:
        for job in jobs:
            if args.skip_existing and job.output_path.exists():
                _sync_final_portrait_output(settings, delivery_config, job.output_path)
                print(f"Skipped existing portrait: {job.output_path}")
                outputs.append(job.output_path)
                continue

            web_config = ChatGPTWebConfig(
                prompt_text=job.prompt_text,
                image_path=job.image_path,
                output_path=job.output_path,
                response_text_path=job.response_text_path,
                profile_dir=args.profile_dir,
                target_url=args.target_url,
                executable_path=args.chrome_exe,
                debug_port=getattr(args, "chrome_debug_port", None),
                launch_timeout_ms=int(args.launch_timeout * 1000),
                result_timeout_ms=int(args.result_timeout * 1000),
                manual_verification_timeout_ms=int(getattr(args, "manual_verification_timeout", 600.0) * 1000),
                submit=not args.no_submit,
                open_new_chat_before_run=portrait_config.new_chat_per_job,
            )
            result = resolved_runner(web_config)
            outputs.append(result or job.output_path)
            if not args.no_submit:
                _sync_final_portrait_output(settings, delivery_config, result or job.output_path)
            if args.no_submit:
                print(f"ChatGPT portrait request prepared: {job.image_path.name} / {job.style.name}")
            else:
                print(f"ChatGPT portrait saved: {job.output_path}")
    finally:
        if session_runner is not None:
            session_runner.close()
    return outputs


def _run_desktop_jobs(
    args: argparse.Namespace,
    jobs: list[PortraitJob],
    portrait_config: PortraitBatchConfig,
    settings: Settings,
    delivery_config: GenerationConfig | None,
) -> list[Path]:
    from api.chatgpt_desktop_v2 import ChatGPTDesktopAgent, DesktopAgentConfig

    backend = getattr(args, "backend", "desktop")
    if backend in {"gemini-desktop", "gemini"}:
        from api.gemini_desktop import (
            DEFAULT_GEMINI_BROWSER_TAB_TITLE_RE,
            GEMINI_APP_URL,
            GeminiDesktopAgent,
        )

        agent_cls = GeminiDesktopAgent
        service_name = "Gemini"
        default_browser_tab_title_re = DEFAULT_GEMINI_BROWSER_TAB_TITLE_RE
        default_target_url: Optional[str] = GEMINI_APP_URL
    else:
        agent_cls = ChatGPTDesktopAgent
        service_name = "ChatGPT"
        default_browser_tab_title_re = DEFAULT_CHATGPT_TAB_TITLE_RE
        default_target_url = None

    outputs: list[Path] = []
    reuse_selected_window = bool(getattr(args, "desktop_reuse_selected_window", False))
    selected_window_handle: int | None = None
    selected_manual_composer_position: tuple[int, int] | None = None
    selected_manual_send_position: tuple[int, int] | None = None
    for job in jobs:
        existing_output_path = _existing_output_for_skip(job) if args.skip_existing else None
        if existing_output_path is not None:
            delivered_path = _sync_final_portrait_output(settings, delivery_config, existing_output_path)
            print(f"Skipped existing portrait: {existing_output_path}")
            if delivered_path is not None and delivered_path != job.output_path:
                print(f"Delivered existing copy: {delivered_path}", flush=True)
            outputs.append(existing_output_path)
            continue
        reactivate_delay = float(getattr(args, "desktop_reactivate_delay", 0.0) or 0.0)
        manual_composer_position: tuple[int, int] | None = None
        manual_send_position: tuple[int, int] | None = selected_manual_send_position if selected_window_handle else None
        if selected_window_handle:
            manual_composer_position = selected_manual_composer_position
        elif reactivate_delay > 0:
            print(
                f"Activate the already-open {service_name} window and click in the message box "
                f"away from the send arrow. Continuing in {reactivate_delay:g} seconds...",
                flush=True,
            )
            time.sleep(reactivate_delay)
            manual_composer_position = _cursor_position()
            print(f"Captured {service_name} message-box point: {manual_composer_position}", flush=True)
        print(
            f"Using existing {service_name} window: {job.display_name} / {job.style.name}",
            flush=True,
        )

        browser_tab_title_re = getattr(args, "desktop_browser_tab_title_re", default_browser_tab_title_re)
        if backend in {"gemini-desktop", "gemini"} and browser_tab_title_re == DEFAULT_CHATGPT_TAB_TITLE_RE:
            browser_tab_title_re = default_browser_tab_title_re
        if getattr(args, "desktop_active_window", False):
            browser_tab_title_re = None

        target_url = None
        if default_target_url:
            configured_target_url = getattr(args, "target_url", None)
            target_url = (
                configured_target_url
                if configured_target_url and configured_target_url != CHATGPT_TARGET_URL
                else default_target_url
            )

        open_new_chat_before_run = getattr(args, "desktop_new_chat", False) or portrait_config.new_chat_per_job
        click_composer_before_paste = getattr(args, "desktop_click_composer", False) or open_new_chat_before_run

        attach_via_clipboard = getattr(args, "desktop_clipboard_attach", False)
        force_clean_chat = job.source_label is not None
        require_new_attachment_preview = bool(job.source_image_paths) and (
            attach_via_clipboard or job.source_label is not None
        )

        config = DesktopAgentConfig(
            image_path=job.image_path,
            image_paths=job.source_image_paths,
            prompt_text=job.prompt_text,
            output_path=job.output_path,
            response_text_path=job.response_text_path,
            executable_path=Path(args.chrome_exe) if getattr(args, "chrome_exe", None) else None,
            window_title_re=getattr(args, "desktop_window_title_re", ".*Google Chrome.*"),
            browser_tab_title_re=browser_tab_title_re,
            target_url=target_url,
            startup_timeout_sec=getattr(args, "launch_timeout", 60.0),
            dialog_timeout_sec=getattr(args, "desktop_dialog_timeout", 20.0),
            result_timeout_sec=getattr(args, "result_timeout", 300.0),
            new_chat_timeout_sec=getattr(args, "desktop_new_chat_timeout", 15.0),
            post_attach_delay_sec=getattr(args, "desktop_post_attach_delay", 3.0),
            min_result_wait_sec=getattr(args, "desktop_min_result_wait", 90.0),
            result_stable_sec=getattr(args, "desktop_result_stable_wait", 8.0),
            open_new_chat_before_run=open_new_chat_before_run,
            force_new_chat_navigation=force_clean_chat,
            allow_new_chat_navigation=not getattr(args, "desktop_no_home_navigation", False),
            use_active_window=getattr(args, "desktop_active_window", False),
            fixed_window_handle=selected_window_handle if reuse_selected_window else None,
            prefer_single_tab_window=getattr(args, "desktop_prefer_single_tab_window", False),
            require_single_tab_window=getattr(args, "desktop_require_single_tab_window", False),
            require_new_attachment_preview=require_new_attachment_preview,
            attach_via_clipboard=attach_via_clipboard,
            skip_capture_result=not getattr(args, "desktop_capture_result", False),
            save_result_via_context_menu=getattr(args, "desktop_save_context_menu", False),
            click_composer_before_paste=click_composer_before_paste,
            manual_composer_position=None if click_composer_before_paste else manual_composer_position,
            manual_send_position=manual_send_position,
            manual_send_capture_delay_sec=getattr(args, "desktop_send_cursor_delay", 0.0),
            mouse_idle_sec=getattr(args, "desktop_wait_mouse_idle_sec", 0.0),
            mouse_idle_timeout_sec=getattr(args, "desktop_mouse_idle_timeout_sec", 60.0),
            verbose=getattr(args, "desktop_verbose", False),
            submit=not args.no_submit,
        )
        try:
            agent = agent_cls(config)
            agent.run()
            if reuse_selected_window:
                selected_window_handle = getattr(agent, "selected_window_handle", None)
                if manual_composer_position is not None:
                    selected_manual_composer_position = manual_composer_position
                selected_manual_send_position = getattr(agent, "selected_manual_send_position", None)
        except Exception as exc:
            if job.output_path.exists() and not args.no_submit:
                try:
                    delivered_path = _sync_final_portrait_output(settings, delivery_config, job.output_path)
                    if delivered_path is not None and delivered_path != job.output_path:
                        print(f"Delivered copy after desktop error: {delivered_path}", flush=True)
                except Exception as delivery_exc:
                    print(
                        f"Delivery failed after desktop error for {job.output_path}: {delivery_exc}",
                        flush=True,
                    )
            unsafe_continue = _is_desktop_unsafe_continue_error(exc)
            if job.source_label is not None:
                print(
                    "Pair desktop safety stop: "
                    f"{job.display_name} / {job.style.name}: {exc}. "
                    "Stopping before the next pair so source photos from different pair folders cannot mix.",
                    flush=True,
                )
                raise
            if _is_desktop_window_selection_error(exc) or unsafe_continue:
                label = "Desktop safety stop" if unsafe_continue else "Desktop window selection failed"
                print(f"{label}: {exc}", flush=True)
                raise
            if not getattr(args, "continue_on_error", False):
                raise
            print(
                f"Desktop job failed: {job.display_name} / {job.style.name}: {exc}",
                flush=True,
            )
            continue
        outputs.append(job.output_path)
        if not args.no_submit:
            delivered_path = _sync_final_portrait_output(settings, delivery_config, job.output_path)
            if delivered_path is not None and delivered_path != job.output_path:
                print(f"Delivered copy: {delivered_path}", flush=True)
        if args.no_submit:
            print(f"Existing {service_name} window prepared: {job.display_name} / {job.style.name}")
        else:
            print(f"Existing {service_name} window saved: {job.output_path}")
        if getattr(args, "pause_between_jobs", False):
            input("Save the generated result manually, then press Enter here to continue...")
    return outputs


def _run_grok_jobs(
    args: argparse.Namespace,
    jobs: list[PortraitJob],
    settings: Settings,
    delivery_config: GenerationConfig | None,
) -> list[Path]:
    from api.grok_web import GrokWebConfig, GrokWebSessionRunner

    profile_dir = _profile_dir_for_backend(getattr(args, "profile_dir", Path(".browser-profile/grok-web")), "grok")
    target_url = getattr(args, "target_url", CHATGPT_TARGET_URL)
    if target_url == CHATGPT_TARGET_URL:
        target_url = "https://grok.com/imagine"

    session_runner = GrokWebSessionRunner()
    outputs: list[Path] = []
    try:
        for job in jobs:
            if args.skip_existing and job.output_path.exists():
                _sync_final_portrait_output(settings, delivery_config, job.output_path)
                print(f"Skipped existing Grok image: {job.output_path}")
                outputs.append(job.output_path)
                continue

            print(f"Using Grok window/profile: {job.image_path.name} / {job.style.name}", flush=True)
            grok_config = GrokWebConfig(
                prompt_text=job.prompt_text,
                image_path=job.image_path,
                output_path=job.output_path,
                profile_dir=profile_dir,
                target_url=target_url,
                executable_path=getattr(args, "chrome_exe", None),
                debug_port=getattr(args, "chrome_debug_port", None),
                launch_timeout_ms=int(getattr(args, "launch_timeout", 60.0) * 1000),
                upload_timeout_ms=int(getattr(args, "upload_timeout", 180.0) * 1000),
                result_timeout_ms=int(getattr(args, "result_timeout", 300.0) * 1000),
                submit=not args.no_submit,
                generation_mode="image",
                aspect_ratio=getattr(args, "grok_aspect_ratio", None),
                orientation=getattr(args, "grok_orientation", None),
                save_debug_artifacts=getattr(args, "save_grok_debug_artifacts", False),
            )
            try:
                result = session_runner.run(grok_config)
            except Exception as exc:
                if not getattr(args, "continue_on_error", False):
                    raise
                print(
                    f"Grok job failed: {job.image_path.name} / {job.style.name}: {exc}",
                    flush=True,
                )
                continue
            outputs.append(result or job.output_path)
            if not args.no_submit:
                _sync_final_portrait_output(settings, delivery_config, result or job.output_path)
            if args.no_submit:
                print(f"Grok image request prepared: {job.image_path.name} / {job.style.name}")
            else:
                print(f"Grok image saved: {job.output_path}")
            if getattr(args, "pause_between_jobs", False):
                input("Review the Grok result, then press Enter here to continue...")
    finally:
        session_runner.close()
    return outputs


def _is_desktop_window_selection_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    markers = (
        "could not find a usable chatgpt browser window",
        "could not find a usable gemini browser window",
        "could not find a chatgpt generation window",
        "could not find a gemini generation window",
        "does not match the generation-window rule",
        "is not the dedicated generation window",
        "is not the chatgpt browser window",
        "is not the gemini browser window",
        "no matching chrome window",
    )
    return any(marker in message for marker in markers)


def _is_desktop_unsafe_continue_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    markers = (
        "browser address-bar focus could not be verified",
        "could not confirm a clean chatgpt chat",
        "could not open a clean new chatgpt chat",
        "gemini did not accept the request",
        "same composer is not filled repeatedly",
        "stopping before enter so the key cannot activate page content",
        "stopping before the next job",
    )
    return any(marker in message for marker in markers)


def _run_local_jobs(
    args: argparse.Namespace,
    jobs: list[PortraitJob],
    settings: Settings,
    delivery_config: GenerationConfig | None,
) -> list[Path]:
    outputs: list[Path] = []
    for job in jobs:
        if args.skip_existing and job.output_path.exists():
            _sync_final_portrait_output(settings, delivery_config, job.output_path)
            print(f"Skipped existing portrait: {job.output_path}")
            outputs.append(job.output_path)
            continue
        if args.no_submit:
            print(f"Local portrait request prepared: {job.image_path.name} / {job.style.name}")
            outputs.append(job.output_path)
            continue
        result = _stylize_locally(job.image_path, job.output_path, job.style.name)
        outputs.append(result)
        _sync_final_portrait_output(settings, delivery_config, result)
        print(f"Local portrait saved: {result}")
    return outputs


def _stylize_locally(image_path: Path, output_path: Path, style_name: str) -> Path:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    normalized_style = style_name.lower()
    if "pastel" in normalized_style:
        styled = _pastel_image(image)
    elif "watercolor" in normalized_style:
        styled = _watercolor_image(image)
    else:
        styled = _watercolor_image(image)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styled.save(output_path, format="PNG")
    return output_path


def _watercolor_image(image: Image.Image) -> Image.Image:
    base = image.filter(ImageFilter.SMOOTH_MORE).filter(ImageFilter.SMOOTH_MORE)
    palette = base.quantize(colors=72, method=Image.Quantize.MEDIANCUT).convert("RGB")
    color = ImageEnhance.Color(palette).enhance(1.18)
    color = ImageEnhance.Contrast(color).enhance(0.88)
    color = ImageEnhance.Brightness(color).enhance(1.05)
    edges = _soft_edge_overlay(image, opacity=0.18)
    wash = Image.blend(color, Image.new("RGB", color.size, (250, 247, 238)), 0.12)
    return ImageChops.multiply(wash, edges)


def _pastel_image(image: Image.Image) -> Image.Image:
    soft = image.filter(ImageFilter.GaussianBlur(radius=1.1))
    color = ImageEnhance.Color(soft).enhance(0.72)
    color = ImageEnhance.Contrast(color).enhance(0.76)
    color = ImageEnhance.Brightness(color).enhance(1.1)
    paper = Image.new("RGB", color.size, (252, 248, 242))
    pastel = Image.blend(color, paper, 0.2)
    edges = _soft_edge_overlay(image, opacity=0.1)
    return ImageChops.multiply(pastel, edges)


def _soft_edge_overlay(image: Image.Image, opacity: float) -> Image.Image:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.6))
    edges = ImageOps.invert(edges).point(lambda value: int(255 - (255 - value) * opacity))
    return Image.merge("RGB", (edges, edges, edges))


def _run_api_jobs(
    args: argparse.Namespace,
    jobs: list[PortraitJob],
    settings: Settings,
    delivery_config: GenerationConfig | None,
) -> list[Path]:
    outputs: list[Path] = []
    for job in jobs:
        if args.skip_existing and job.output_path.exists():
            _sync_final_portrait_output(settings, delivery_config, job.output_path)
            print(f"Skipped existing portrait: {job.output_path}")
            outputs.append(job.output_path)
            continue
        if args.no_submit:
            print(f"OpenAI portrait request prepared: {job.image_path.name} / {job.style.name}")
            outputs.append(job.output_path)
            continue

        metadata = analyze_image(job.image_path)
        stage_id = job.output_path.stem
        result = edit_image_with_openai(
            job.image_path,
            job.style.name,
            job.output_path,
            metadata,
            stage_id,
            prompt_override=job.prompt_text,
            model_name=getattr(args, "api_model", None),
        )
        outputs.append(result)
        _sync_final_portrait_output(settings, delivery_config, result)
        print(f"OpenAI portrait saved: {result}")
    return outputs


def main() -> None:
    args = parse_args()
    outputs = run_batch(args)
    print(f"Processed portraits: {len(outputs)}")


if __name__ == "__main__":
    try:
        main()
    except PortraitConfigError as exc:
        raise SystemExit(f"Portrait config error: {exc}") from exc
