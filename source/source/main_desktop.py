from __future__ import annotations

import argparse
from pathlib import Path

from api.chatgpt_desktop_v2 import ChatGPTDesktopAgent, DesktopAgentConfig
from config import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Windows MVP agent for automating the ChatGPT desktop app."
    )
    parser.add_argument(
        "--image",
        "-i",
        required=True,
        help="Source image path. Relative paths are resolved against input/.",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        required=True,
        help="Prompt file path. Relative paths are resolved against input/.",
    )
    parser.add_argument(
        "--chatgpt-exe",
        type=str,
        default=None,
        help="Optional full path to ChatGPT.exe. If omitted, the agent connects to an existing ChatGPT window.",
    )
    parser.add_argument(
        "--window-title-re",
        type=str,
        default=".*Google Chrome.*",
        help="Regex used to find the browser window.",
    )
    parser.add_argument(
        "--browser-tab-title-re",
        type=str,
        default=".*ChatGPT.*",
        help="Regex used to find the ChatGPT Web tab inside Chrome.",
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default="https://chatgpt.com/",
        help="Optional URL to open if the ChatGPT tab is not active.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for the ChatGPT window to appear.",
    )
    parser.add_argument(
        "--dialog-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for the Windows open-file dialog.",
    )
    parser.add_argument(
        "--result-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for the generated image to appear in the ChatGPT window.",
    )
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="Populate the image and prompt fields but do not submit the request.",
    )
    return parser.parse_args()


def resolve_input_path(base_dir: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate


def load_prompt(prompt_path: Path) -> str:
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file was not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def main() -> None:
    args = parse_args()
    settings = Settings()
    settings.ensure_output()

    image_path = resolve_input_path(settings.input_dir, args.image)
    prompt_path = resolve_input_path(settings.input_dir, args.prompt)
    if not image_path.exists():
        raise FileNotFoundError(f"Source image was not found: {image_path}")
    output_path = settings.output_dir / f"{image_path.stem}_desktop_result.png"

    config = DesktopAgentConfig(
        image_path=image_path,
        prompt_text=load_prompt(prompt_path),
        output_path=output_path,
        executable_path=Path(args.chatgpt_exe) if args.chatgpt_exe else None,
        window_title_re=args.window_title_re,
        browser_tab_title_re=args.browser_tab_title_re,
        target_url=args.target_url,
        startup_timeout_sec=args.startup_timeout,
        dialog_timeout_sec=args.dialog_timeout,
        result_timeout_sec=args.result_timeout,
        submit=not args.no_submit,
    )
    ChatGPTDesktopAgent(config).run()
    print(f"Desktop agent completed successfully. Result saved to: {output_path}")


if __name__ == "__main__":
    main()
