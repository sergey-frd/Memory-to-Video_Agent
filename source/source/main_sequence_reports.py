from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from config import Settings
from utils.current_sequence_reports import (
    derive_current_sequence_report_bundle_paths,
    write_current_sequence_report_bundle,
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
        description="Rebuild music-first, structure, and transition reports from the current order of a Premiere sequence."
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
        "--output-music-txt",
        type=Path,
        default=None,
        help="Optional output path for the music-first recommendation report.",
    )
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
    parser.add_argument(
        "--music-only",
        action="store_true",
        help="Build only the JSON context and the music recommendation report, without structure and transition reports.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    settings = Settings()
    settings.ensure_output()

    default_json_path, default_music_path, default_structure_path, default_transition_path = derive_current_sequence_report_bundle_paths(
        sequence_name=args.sequence_name,
        optimization_report_json=args.optimization_report_json,
        output_dir=args.output_dir,
    )
    output_json = args.output_json or default_json_path
    output_music_txt = args.output_music_txt or default_music_path
    output_structure_txt = args.output_structure_txt or default_structure_path
    output_transition_txt = args.output_transition_txt or default_transition_path

    rebuilt_json, rebuilt_music_txt, rebuilt_structure_txt, rebuilt_transition_txt = write_current_sequence_report_bundle(
        project_path=args.prproj,
        sequence_name=args.sequence_name,
        optimization_report_json=args.optimization_report_json,
        output_json=output_json,
        output_music_txt=output_music_txt,
        output_structure_txt=None if args.music_only else output_structure_txt,
        output_transition_txt=None if args.music_only else output_transition_txt,
        include_music=True,
        include_structure=not args.music_only,
        include_transition=not args.music_only,
    )

    print(f"Current-order JSON saved to: {rebuilt_json}")
    if rebuilt_music_txt is not None:
        print(f"Current-order music recommendation report saved to: {rebuilt_music_txt}")
    if rebuilt_structure_txt is not None:
        print(f"Current-order structure report saved to: {rebuilt_structure_txt}")
    if rebuilt_transition_txt is not None:
        print(f"Current-order transition recommendations saved to: {rebuilt_transition_txt}")
    if args.music_only:
        print("Structure and transition reports were skipped due to: --music-only")


if __name__ == "__main__":
    main()
