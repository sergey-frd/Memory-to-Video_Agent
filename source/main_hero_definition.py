from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from utils.hero_definition import run_hero_definition_from_config


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
        description="Create a reusable hero identity definition from reference images and a text profile."
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to hero-definition JSON config.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    output_path = run_hero_definition_from_config(args.config)
    print(f"Hero definition JSON: {output_path}")


if __name__ == "__main__":
    main()
