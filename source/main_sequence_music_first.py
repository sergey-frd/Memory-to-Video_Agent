from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from config import Settings
from utils.project_sequence_reports_from_project import (
    derive_project_sequence_music_first_bundle_paths,
    write_project_sequence_music_first_bundle,
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
            "Build a music-first recommendation report directly from a Premiere project and sequence, "
            "without requiring a prior optimization JSON."
        )
    )
    parser.add_argument("--prproj", type=Path, required=True, help="Path to the Premiere project (.prproj).")
    parser.add_argument("--sequence-name", type=str, required=True, help="Premiere sequence name to inspect.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to the configured output directory.",
    )
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path.")
    parser.add_argument("--output-music-txt", type=Path, default=None, help="Optional output path for the music report.")
    parser.add_argument(
        "--output-structure-txt",
        type=Path,
        default=None,
        help="Optional output path for the recommended sequence structure/order report.",
    )
    parser.add_argument(
        "--output-transition-txt",
        type=Path,
        default=None,
        help="Optional output path for the transition recommendations report.",
    )
    parser.add_argument(
        "--max-sampled-clips",
        type=int,
        default=12,
        help="Maximum number of representative clips to sample from the current sequence.",
    )
    parser.add_argument(
        "--max-analyzed-clips",
        type=int,
        default=None,
        help=(
            "Optional cap for the number of clips analyzed when the full recommendation mode is enabled. "
            "Omit to analyze all current sequence clips."
        ),
    )
    parser.add_argument(
        "--full-recommendations",
        action="store_true",
        help=(
            "After the primary music report, also generate the recommended sequence/order report and the "
            "transition recommendations report."
        ),
    )
    parser.add_argument(
        "--scene-model",
        type=str,
        default=None,
        help="Optional OpenAI model override for representative frame scene analysis.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    settings = Settings()
    settings.ensure_output()

    default_output_json, default_output_music, default_output_structure, default_output_transition = (
        derive_project_sequence_music_first_bundle_paths(
            project_path=args.prproj,
            sequence_name=args.sequence_name,
            output_dir=args.output_dir or settings.output_dir,
        )
    )
    output_json = args.output_json or default_output_json
    output_music = args.output_music_txt or default_output_music
    include_structure = args.full_recommendations or args.output_structure_txt is not None
    include_transition = args.full_recommendations or args.output_transition_txt is not None
    output_structure = args.output_structure_txt or default_output_structure
    output_transition = args.output_transition_txt or default_output_transition

    written_json, written_music, written_structure, written_transition = write_project_sequence_music_first_bundle(
        project_path=args.prproj,
        sequence_name=args.sequence_name,
        output_json=output_json,
        output_music_txt=output_music,
        output_structure_txt=output_structure if include_structure else None,
        output_transition_txt=output_transition if include_transition else None,
        include_structure=include_structure,
        include_transition=include_transition,
        max_sampled_clips=args.max_sampled_clips,
        max_analyzed_clips=args.max_analyzed_clips,
        scene_model=args.scene_model,
        settings=settings,
    )

    print(f"Project-sequence JSON saved to: {written_json}")
    print(f"Music-first recommendation report saved to: {written_music}")
    if written_structure is not None:
        print(f"Recommended sequence/order report saved to: {written_structure}")
    if written_transition is not None:
        print(f"Transition recommendations report saved to: {written_transition}")


if __name__ == "__main__":
    main()
