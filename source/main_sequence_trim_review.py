from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from utils.sequence_trim_review import run_sequence_trim_review_from_config


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
        description=(
            "Prototype: read a Premiere sequence, split every clip into KEEP/DROP segments "
            "toward a 3-5 minute budget, and export a review .prproj with segments on separate tracks."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to trim-review JSON config.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    json_path, txt_path, project_path = run_sequence_trim_review_from_config(args.config)
    print(f"Trim review bundle JSON: {json_path}")
    print(f"Trim review bundle report: {txt_path}")
    if project_path is not None:
        print(f"Trim review project: {project_path}")
    else:
        print("Trim review project: skipped (write_project=false)")


if __name__ == "__main__":
    main()
