from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from utils.sequence_presentation import derive_sequence_presentation_path, write_sequence_presentation


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
        description="Build a visual HTML presentation from manual-order structure and transition reports."
    )
    parser.add_argument(
        "--optimization-report-json",
        type=Path,
        required=True,
        help="Path to the manual_order JSON with clip metadata and asset paths.",
    )
    parser.add_argument(
        "--structure-report-txt",
        type=Path,
        required=True,
        help="Path to the manual_order structure report text file.",
    )
    parser.add_argument(
        "--transition-report-txt",
        type=Path,
        required=True,
        help="Path to the manual_order transition recommendations text file.",
    )
    parser.add_argument(
        "--output-html",
        type=Path,
        default=None,
        help="Optional output HTML path. Defaults beside the structure report.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Optional custom page title.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    output_html = args.output_html or derive_sequence_presentation_path(
        structure_report_txt=args.structure_report_txt,
    )
    written = write_sequence_presentation(
        optimization_report_json=args.optimization_report_json,
        structure_report_txt=args.structure_report_txt,
        transition_report_txt=args.transition_report_txt,
        output_path=output_html,
        title=args.title,
    )
    print(f"Sequence presentation saved to: {written}")


if __name__ == "__main__":
    main()
