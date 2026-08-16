from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from utils.sequence_import_and_keep import (
    is_import_and_keep_config,
    run_sequence_import_and_keep_from_config,
)
from utils.sequence_keep_apply import is_keep_apply_config, run_sequence_keep_apply_from_config
from utils.sequence_media_import import is_media_import_config, run_sequence_media_import_from_config
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
            "or hero-presence analysis, export a review .prproj, replay a saved report, or apply "
            "manual keep-range JSON to create a trimmed copy of the project, import listed "
            "media files onto a sequence, or import and keep-trim in one pass."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to trim-review JSON config.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    try:
        payload = json.loads(args.config.read_text(encoding="utf-8"))
        mode = str(payload.get("mode") or "").strip().casefold()
        if mode == "report_replay":
            json_path, txt_path, project_path = run_sequence_trim_report_replay_from_config(
                args.config
            )
            label = "Trim review"
        elif is_import_and_keep_config(payload):
            json_path, txt_path, project_path = run_sequence_import_and_keep_from_config(
                args.config
            )
            label = "Import and keep"
        elif is_media_import_config(payload):
            json_path, txt_path, project_path = run_sequence_media_import_from_config(args.config)
            label = "Media import"
        elif is_keep_apply_config(payload):
            json_path, txt_path, project_path = run_sequence_keep_apply_from_config(args.config)
            label = "Keep apply"
        else:
            json_path, txt_path, project_path = run_sequence_trim_review_from_config(args.config)
            label = "Trim review"
    except KeyboardInterrupt:
        print(
            "\nTrim review interrupted by user. Run the same command again to restart. "
            "The hero engine resumes completed clips when its cache is enabled.",
            flush=True,
        )
        raise SystemExit(130) from None
    print(f"{label} bundle JSON: {json_path}")
    print(f"{label} bundle report: {txt_path}")
    if project_path is not None:
        print(f"{label} project: {project_path}")
    else:
        print(f"{label} project: skipped (write_project=false)")


if __name__ == "__main__":
    main()
