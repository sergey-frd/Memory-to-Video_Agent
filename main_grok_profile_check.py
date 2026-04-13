from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from api.grok_web import GrokWebAgent, GrokWebConfig, GrokWebError


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
        description="Verify that the clone-specific Grok Chrome profile is already authenticated and ready."
    )
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile/grok-web"), help="Persistent Chrome profile for Grok Web.")
    parser.add_argument("--target-url", type=str, default="https://grok.com/imagine", help="Grok Web URL.")
    parser.add_argument("--chrome-exe", type=Path, default=None, help="Optional explicit Chrome executable path.")
    parser.add_argument("--chrome-debug-port", type=int, default=None, help="Optional Chrome remote debugging port.")
    parser.add_argument("--launch-timeout", type=float, default=60.0, help="How long to wait for Grok Web to open, in seconds.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> GrokWebConfig:
    return GrokWebConfig(
        prompt_text="Grok profile authentication check.",
        image_path=None,
        output_path=Path(".grok_profile_check_unused"),
        profile_dir=args.profile_dir,
        target_url=args.target_url,
        executable_path=args.chrome_exe,
        debug_port=args.chrome_debug_port,
        launch_timeout_ms=int(args.launch_timeout * 1000),
        upload_timeout_ms=1_000,
        result_timeout_ms=1_000,
        submit=False,
    )


def main() -> None:
    _configure_stdio()
    args = parse_args()
    config = build_config(args)
    GrokWebAgent(config).check_authentication()
    print(f"Grok profile authentication is active: {config.profile_dir}")


if __name__ == "__main__":
    try:
        main()
    except GrokWebError as exc:
        raise SystemExit(str(exc)) from exc
