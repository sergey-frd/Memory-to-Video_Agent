from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from main_premiere_import_keep import try_run_premiere_import_keep
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
            "media files onto a sequence, import into a new sequence in the same project, "
            "copy a source sequence and KEEP-trim the copy, or import and keep-trim in one pass."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to trim-review JSON config.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    try:
        try:
            payload = json.loads(args.config.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {args.config}: {exc}. "
                "Windows paths must use doubled backslashes, "
                r'e.g. "<LOCAL_PATH>".'
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Config {args.config} must be a JSON object.")
        mode = str(payload.get("mode") or "").strip().casefold()
        if mode == "report_replay":
            json_path, txt_path, project_path = run_sequence_trim_report_replay_from_config(
                args.config
            )
            label = "Trim review"
        else:
            import_keep = try_run_premiere_import_keep(args.config, payload)
            if import_keep is not None:
                label, json_path, txt_path, project_path = import_keep
            else:
                json_path, txt_path, project_path = run_sequence_trim_review_from_config(
                    args.config
                )
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
