from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils.premiere_transition_script import write_premiere_transition_extendscript


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Premiere ExtendScript file that applies visible transitions inside Premiere."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--config", type=Path, help="Project sequence batch JSON config.")
    source_group.add_argument("--prproj", type=Path, help="Optimized Premiere .prproj to inspect.")
    parser.add_argument("--sequence-name", type=str, default=None, help="Target sequence name for --prproj mode.")
    parser.add_argument("--output-jsx", type=Path, default=None, help="Output .jsx path in --prproj mode.")
    parser.add_argument(
        "--optimization-report-json",
        type=Path,
        default=None,
        help="Optional optimizer JSON whose transition_to_next plans should drive per-cut transition names.",
    )
    parser.add_argument("--transition-name", type=str, default="Cross Dissolve", help="Premiere transition display name.")
    parser.add_argument("--duration-seconds", type=float, default=1.0, help="Transition duration in seconds.")
    parser.add_argument("--track-index", type=int, default=0, help="Zero-based Premiere video track index.")
    parser.add_argument("--no-save-project", action="store_true", help="Do not call app.project.save() from JSX.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.config is not None:
        output_paths = _write_from_batch_config(args)
    else:
        if args.sequence_name is None:
            raise SystemExit("--sequence-name is required with --prproj")
        if args.output_jsx is None:
            raise SystemExit("--output-jsx is required with --prproj")
        output_path, jobs = write_premiere_transition_extendscript(
            project_path=args.prproj,
            sequence_name=args.sequence_name,
            output_jsx_path=args.output_jsx,
            transition_name=args.transition_name,
            duration_seconds=args.duration_seconds,
            video_track_index=args.track_index,
            save_project=not args.no_save_project,
            optimization_report_json=args.optimization_report_json,
        )
        output_paths = [(output_path, len(jobs))]

    for output_path, job_count in output_paths:
        print(f"Generated Premiere transition script: {output_path} ({job_count} transition job(s))")


def _write_from_batch_config(args: argparse.Namespace) -> list[tuple[Path, int]]:
    payload = json.loads(args.config.read_text(encoding="utf-8"))
    project_path = Path(str(payload["output_project_path"]))
    reports_dir = Path(str(payload.get("reports_dir") or args.config.parent))
    sequence_jobs = payload.get("sequence_jobs") or []
    if not sequence_jobs:
        raise SystemExit(f"Batch config does not contain sequence_jobs: {args.config}")

    output_paths: list[tuple[Path, int]] = []
    for index, raw_job in enumerate(sequence_jobs, start=1):
        sequence_name = str(raw_job.get("new_sequence_name") or raw_job["source_sequence_name"])
        output_jsx_path = reports_dir / f"{_slugify_filename(sequence_name)}_apply_transitions.jsx"
        report_json_path = reports_dir / f"{index:02d}_{_slugify_filename(sequence_name)}.json"
        output_path, jobs = write_premiere_transition_extendscript(
            project_path=project_path,
            sequence_name=sequence_name,
            output_jsx_path=output_jsx_path,
            transition_name=args.transition_name,
            duration_seconds=args.duration_seconds,
            video_track_index=args.track_index,
            save_project=not args.no_save_project,
            optimization_report_json=report_json_path if report_json_path.exists() else None,
        )
        output_paths.append((output_path, len(jobs)))
    return output_paths


def _slugify_filename(value: str) -> str:
    safe_chars = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_", "."):
            safe_chars.append(char)
        elif char.isspace():
            safe_chars.append("_")
    return "".join(safe_chars).strip("._") or "sequence"


if __name__ == "__main__":
    main()
