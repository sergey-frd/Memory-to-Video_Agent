from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from utils.human_profile_sequence_report import (
    derive_human_profile_report_path,
    write_human_profile_sequence_report_from_json,
)


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
            "Build a personalized report that combines a video-based sequence report with "
            "a human-written hero description."
        )
    )
    parser.add_argument(
        "--optimization-report-json",
        type=Path,
        required=True,
        help="Path to an existing sequence optimization JSON or manual-order JSON.",
    )
    parser.add_argument(
        "--human-detail-txt",
        type=Path,
        required=True,
        help="Path to a human-written text profile for the hero.",
    )
    parser.add_argument(
        "--output-report-txt",
        type=Path,
        default=None,
        help="Optional output path for the personalized human-aware report.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    output_path = args.output_report_txt or derive_human_profile_report_path(args.optimization_report_json)
    final_path = write_human_profile_sequence_report_from_json(
        optimization_report_json=args.optimization_report_json,
        human_detail_txt=args.human_detail_txt,
        output_path=output_path,
    )
    print(f"Human-aware report saved to: {final_path}")


if __name__ == "__main__":
    main()
