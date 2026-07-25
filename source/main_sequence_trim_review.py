from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from utils.sequence_trim_report_replay import run_sequence_trim_report_replay_from_config
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
            "Read a Premiere sequence, split clips into KEEP/DROP segments using budget, semantic, "
            "or hero-presence analysis, and export a review .prproj with segments on separate tracks."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to trim-review JSON config.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    try:
        payload = json.loads(args.config.read_text(encoding="utf-8"))
        if str(payload.get("mode") or "").strip().casefold() == "report_replay":
            json_path, txt_path, project_path = run_sequence_trim_report_replay_from_config(
                args.config
            )
        else:
            json_path, txt_path, project_path = run_sequence_trim_review_from_config(args.config)
    except KeyboardInterrupt:
        print(
            "\nTrim review interrupted by user. Run the same command again to restart. "
            "The hero engine resumes completed clips when its cache is enabled.",
            flush=True,
        )
        raise SystemExit(130) from None
    print(f"Trim review bundle JSON: {json_path}")
    print(f"Trim review bundle report: {txt_path}")
    if project_path is not None:
        print(f"Trim review project: {project_path}")
    else:
        print("Trim review project: skipped (write_project=false)")


if __name__ == "__main__":
    main()
