from __future__ import annotations

import argparse
import ctypes
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from api.openai_image import edit_image_with_openai
from api.chatgpt_web import ChatGPTWebConfig, ChatGPTWebSessionRunner
from config import Settings
from utils.image_analysis import analyze_image

SUPPORTED_INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DEFAULT_CONFIG_NAME = "chatgpt_portrait_config.json"
DEFAULT_OUTPUT_SUBDIR = "chatgpt_portraits"
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


PortraitRunner = Callable[[ChatGPTWebConfig], Optional[Path]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate styled portraits in ChatGPT Web for all images from input/."
    )
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory with source images. Defaults to input/.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for generated portraits.")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help=f"Portrait config JSON. Defaults to {DEFAULT_CONFIG_NAME}.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".browser-profile/chatgpt-web"),
        help="Persistent Chrome profile for ChatGPT Web.",
    )
    parser.add_argument("--target-url", type=str, default="https://chatgpt.com/", help="ChatGPT Web URL.")
    parser.add_argument("--chrome-exe", type=Path, default=None, help="Optional explicit Chrome executable path.")
    parser.add_argument(
        "--chrome-debug-port",
        type=int,
        default=None,
        help="Connect to an already opened Chrome session instead of launching a new automation window.",
    )
    parser.add_argument(
        "--backend",
        choices=("web", "desktop", "api", "local"),
        default="web",
        help="Use ChatGPT Web automation, an already-open desktop Chrome/ChatGPT window, the official OpenAI Images API, or local stylization.",
    )
    parser.add_argument(
        "--api-model",
        type=str,
        default=None,
        help="OpenAI image edit model for --backend api, for example gpt-image-1.5 or dall-e-2.",
    )
    parser.add_argument("--result-timeout", type=float, default=300.0, help="Seconds to wait for each portrait.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="Seconds to wait for ChatGPT Web to open.")
    parser.add_argument("--desktop-window-title-re", type=str, default=".*Google Chrome.*", help="Regex for the existing browser window.")
    parser.add_argument("--desktop-browser-tab-title-re", type=str, default=".*ChatGPT.*", help="Regex for the existing ChatGPT browser tab.")
    parser.add_argument("--desktop-dialog-timeout", type=float, default=20.0, help="Seconds to wait for the Windows open-file dialog.")
    parser.add_argument("--desktop-new-chat-timeout", type=float, default=15.0, help="Seconds to wait while opening a new ChatGPT chat.")
    parser.add_argument("--desktop-active-window", action="store_true", help="Use the currently active window instead of searching Chrome windows/tabs.")
    parser.add_argument("--desktop-prefer-single-tab-window", action="store_true", help="Prefer a ChatGPT Chrome window with exactly one visible tab when several ChatGPT windows are open.")
    parser.add_argument("--desktop-require-single-tab-window", action="store_true", help="Only use a ChatGPT Chrome window with exactly one visible tab; fail fast if the selected window has extra tabs.")
    parser.add_argument("--desktop-new-chat", action="store_true", help="Try to open a new ChatGPT chat before every desktop job.")
    parser.add_argument("--desktop-clipboard-attach", action="store_true", help="Attach images by pasting the file from Windows clipboard into the active ChatGPT composer.")
    parser.add_argument("--desktop-capture-result", action="store_true", help="Capture a generated image from the desktop UI after submitting.")
    parser.add_argument("--desktop-save-context-menu", action="store_true", help="Try to save the generated image through the browser image context menu.")
    parser.add_argument("--desktop-reactivate-delay", type=float, default=0.0, help="Seconds to wait before each desktop job so you can activate the ChatGPT composer.")
    parser.add_argument("--desktop-send-cursor-delay", type=float, default=0.0, help="Seconds to wait after pasting so you can move the mouse over the active ChatGPT send arrow.")
    parser.add_argument("--desktop-click-composer", action="store_true", help="Click an estimated ChatGPT composer position before pasting. Off by default for active-window mode.")
    parser.add_argument("--desktop-post-attach-delay", type=float, default=3.0, help="Seconds to wait after pasting the source image into ChatGPT.")
    parser.add_argument("--desktop-min-result-wait", type=float, default=90.0, help="Minimum seconds to wait after submitting before auto-saving a result image.")
    parser.add_argument("--desktop-result-stable-wait", type=float, default=8.0, help="Seconds a result image must stay stable before auto-saving.")
    parser.add_argument("--pause-between-jobs", action="store_true", help="After each submitted job, wait for Enter before continuing.")
    parser.add_argument("--desktop-verbose", action="store_true", help="Print detailed desktop automation progress.")
    parser.add_argument(
        "--manual-verification-timeout",
        type=float,
        default=600.0,
        help="Seconds to wait while you manually complete ChatGPT sign-in or human verification.",
    )
    parser.add_argument("--no-submit", action="store_true", help="Fill each ChatGPT request without submitting it.")
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
        help="Save visible ChatGPT response text next to portrait outputs.",
    )
    parser.add_argument(
        "--skip-response-text",
        action="store_false",
        dest="save_response_text",
        help="Do not save ChatGPT response text even if enabled in config.",
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
    if "portrait_styles" in data and "styles" in data:
        raise PortraitConfigError("Use either 'portrait_styles' or 'styles', not both.")

    styles_payload = data.get("portrait_styles", data.get("styles"))
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


def _render_prompt(template: str, *, style: str, image_path: Path) -> str:
    try:
        return template.format(style=style, image_name=image_path.name, image_stem=image_path.stem)
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


def run_batch(
    args: argparse.Namespace,
    settings: Settings | None = None,
    runner: PortraitRunner | None = None,
) -> list[Path]:
    settings = settings or Settings()
    settings.ensure_output()

    config_path = args.config_file or (settings.project_root / DEFAULT_CONFIG_NAME)
    portrait_config = load_portrait_config(config_path)
    input_dir = args.input_dir or settings.input_dir
    output_dir = _resolve_under_project(args.output_dir, settings)
    if output_dir is None:
        output_dir = _resolve_under_project(portrait_config.output_dir, settings)
    if output_dir is None:
        output_dir = settings.output_dir / DEFAULT_OUTPUT_SUBDIR

    save_response_text = portrait_config.save_response_text
    cli_save_response_text = getattr(args, "save_response_text", None)
    if cli_save_response_text is not None:
        save_response_text = cli_save_response_text
    response_text_dir = None
    if save_response_text:
        response_text_dir = _resolve_under_project(portrait_config.response_text_dir, settings) or output_dir

    images = list_input_images(input_dir)
    if not images:
        raise FileNotFoundError(f"No source images found in: {input_dir}")
    jobs = build_portrait_jobs(images, portrait_config, output_dir, response_text_dir)
    if getattr(args, "backend", "web") == "desktop":
        return _run_desktop_jobs(args, jobs, portrait_config)
    if getattr(args, "backend", "web") == "local":
        return _run_local_jobs(args, jobs)
    if getattr(args, "backend", "web") == "api":
        return _run_api_jobs(args, jobs)

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
) -> list[Path]:
    from api.chatgpt_desktop_v2 import ChatGPTDesktopAgent, DesktopAgentConfig

    outputs: list[Path] = []
    for job in jobs:
        if args.skip_existing and job.output_path.exists():
            print(f"Skipped existing portrait: {job.output_path}")
            outputs.append(job.output_path)
            continue
        reactivate_delay = float(getattr(args, "desktop_reactivate_delay", 0.0) or 0.0)
        manual_composer_position: tuple[int, int] | None = None
        manual_send_position: tuple[int, int] | None = None
        if reactivate_delay > 0:
            print(
                "Activate the already-open ChatGPT window and click in the message box "
                f"away from the send arrow. Continuing in {reactivate_delay:g} seconds...",
                flush=True,
            )
            time.sleep(reactivate_delay)
            manual_composer_position = _cursor_position()
            print(f"Captured ChatGPT message-box point: {manual_composer_position}", flush=True)
        print(
            f"Using existing ChatGPT window: {job.image_path.name} / {job.style.name}",
            flush=True,
        )

        config = DesktopAgentConfig(
            image_path=job.image_path,
            prompt_text=job.prompt_text,
            output_path=job.output_path,
            response_text_path=job.response_text_path,
            executable_path=Path(args.chrome_exe) if getattr(args, "chrome_exe", None) else None,
            window_title_re=getattr(args, "desktop_window_title_re", ".*Google Chrome.*"),
            browser_tab_title_re=None
            if getattr(args, "desktop_active_window", False)
            else getattr(args, "desktop_browser_tab_title_re", ".*ChatGPT.*"),
            target_url=None,
            startup_timeout_sec=getattr(args, "launch_timeout", 60.0),
            dialog_timeout_sec=getattr(args, "desktop_dialog_timeout", 20.0),
            result_timeout_sec=getattr(args, "result_timeout", 300.0),
            new_chat_timeout_sec=getattr(args, "desktop_new_chat_timeout", 15.0),
            post_attach_delay_sec=getattr(args, "desktop_post_attach_delay", 3.0),
            min_result_wait_sec=getattr(args, "desktop_min_result_wait", 90.0),
            result_stable_sec=getattr(args, "desktop_result_stable_wait", 8.0),
            open_new_chat_before_run=getattr(args, "desktop_new_chat", False)
            or portrait_config.new_chat_per_job,
            use_active_window=getattr(args, "desktop_active_window", False),
            prefer_single_tab_window=getattr(args, "desktop_prefer_single_tab_window", False),
            require_single_tab_window=getattr(args, "desktop_require_single_tab_window", False),
            attach_via_clipboard=getattr(args, "desktop_clipboard_attach", False),
            skip_capture_result=not getattr(args, "desktop_capture_result", False),
            save_result_via_context_menu=getattr(args, "desktop_save_context_menu", False),
            click_composer_before_paste=getattr(args, "desktop_click_composer", False),
            manual_composer_position=manual_composer_position,
            manual_send_position=manual_send_position,
            manual_send_capture_delay_sec=getattr(args, "desktop_send_cursor_delay", 0.0),
            verbose=getattr(args, "desktop_verbose", False),
            submit=not args.no_submit,
        )
        try:
            ChatGPTDesktopAgent(config).run()
        except Exception as exc:
            if _is_desktop_window_selection_error(exc):
                print(
                    f"Desktop window selection failed: {exc}",
                    flush=True,
                )
                raise
            if not getattr(args, "continue_on_error", False):
                raise
            print(
                f"Desktop job failed: {job.image_path.name} / {job.style.name}: {exc}",
                flush=True,
            )
            continue
        outputs.append(job.output_path)
        if args.no_submit:
            print(f"Existing ChatGPT window prepared: {job.image_path.name} / {job.style.name}")
        else:
            print(f"Existing ChatGPT window saved: {job.output_path}")
        if getattr(args, "pause_between_jobs", False):
            input("Save the generated result manually, then press Enter here to continue...")
    return outputs


def _is_desktop_window_selection_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    markers = (
        "could not find a usable chatgpt browser window",
        "could not find a chatgpt generation window",
        "does not match the generation-window rule",
        "is not the dedicated generation window",
        "is not the chatgpt browser window",
        "no matching chrome window",
    )
    return any(marker in message for marker in markers)


def _run_local_jobs(args: argparse.Namespace, jobs: list[PortraitJob]) -> list[Path]:
    outputs: list[Path] = []
    for job in jobs:
        if args.skip_existing and job.output_path.exists():
            print(f"Skipped existing portrait: {job.output_path}")
            outputs.append(job.output_path)
            continue
        if args.no_submit:
            print(f"Local portrait request prepared: {job.image_path.name} / {job.style.name}")
            outputs.append(job.output_path)
            continue
        result = _stylize_locally(job.image_path, job.output_path, job.style.name)
        outputs.append(result)
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


def _run_api_jobs(args: argparse.Namespace, jobs: list[PortraitJob]) -> list[Path]:
    outputs: list[Path] = []
    for job in jobs:
        if args.skip_existing and job.output_path.exists():
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
