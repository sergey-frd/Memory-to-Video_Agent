from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from config import Settings
from utils.current_sequence_reports import (
    derive_current_sequence_report_paths,
    write_current_sequence_reports,
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
        description="Rebuild structure, video description, music, and transition reports from the current order of a Premiere sequence."
    )
    parser.add_argument("--prproj", type=Path, required=True, help="Path to the Premiere project (.prproj).")
    parser.add_argument("--sequence-name", type=str, required=True, help="Premiere sequence name to inspect.")
    parser.add_argument(
        "--optimization-report-json",
        type=Path,
        required=True,
        help="Existing optimization JSON with candidate metadata for the same sequence.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to the directory of --optimization-report-json.",
    )
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path for the rebuilt current-order context.")
    parser.add_argument(
        "--output-structure-txt",
        type=Path,
        default=None,
        help="Optional output path for the rebuilt sequence structure report.",
    )
    parser.add_argument(
        "--output-transition-txt",
        type=Path,
        default=None,
        help="Optional output path for the rebuilt transition recommendations report.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    settings = Settings()
    settings.ensure_output()

    default_json_path, default_structure_path, default_transition_path = derive_current_sequence_report_paths(
        sequence_name=args.sequence_name,
        optimization_report_json=args.optimization_report_json,
        output_dir=args.output_dir,
    )
    output_json = args.output_json or default_json_path
    output_structure_txt = args.output_structure_txt or default_structure_path
    output_transition_txt = args.output_transition_txt or default_transition_path

    rebuilt_json, rebuilt_structure_txt, rebuilt_transition_txt = write_current_sequence_reports(
        project_path=args.prproj,
        sequence_name=args.sequence_name,
        optimization_report_json=args.optimization_report_json,
        output_json=output_json,
        output_structure_txt=output_structure_txt,
        output_transition_txt=output_transition_txt,
    )

    print(f"Current-order JSON saved to: {rebuilt_json}")
    print(f"Current-order structure report saved to: {rebuilt_structure_txt}")
    print(f"Current-order transition recommendations saved to: {rebuilt_transition_txt}")


if __name__ == "__main__":
    main()
