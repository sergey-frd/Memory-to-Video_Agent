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
from utils.premiere_sequence_motion import (
    is_premiere_sequence_motion_config,
    run_premiere_sequence_motion_from_config,
)
from utils.premiere_sequence_insert_motion import (
    is_premiere_sequence_insert_motion_config,
    run_premiere_sequence_insert_motion_from_config,
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


def try_run_premiere_import_keep(
    config_path: Path,
    payload: object,
    *,
    dry_run: bool = False,
) -> tuple[str, Path, Path, Path | None] | None:
    if is_premiere_sequence_insert_motion_config(payload):
        json_path, txt_path, project_path = (
            run_premiere_sequence_insert_motion_from_config(
                config_path,
                dry_run_only=dry_run,
            )
        )
        return "Premiere sequence insert and motion", json_path, txt_path, project_path
    if is_premiere_sequence_motion_config(payload):
        json_path, txt_path, project_path = run_premiere_sequence_motion_from_config(
            config_path,
            dry_run_only=dry_run,
        )
        return "Premiere sequence motion", json_path, txt_path, project_path
    if is_import_and_keep_config(payload):
        json_path, txt_path, project_path = run_sequence_import_and_keep_from_config(config_path)
        return "Import and keep", json_path, txt_path, project_path
    if is_media_import_config(payload):
        json_path, txt_path, project_path = run_sequence_media_import_from_config(config_path)
        return "Media import", json_path, txt_path, project_path
    if is_keep_apply_config(payload):
        json_path, txt_path, project_path = run_sequence_keep_apply_from_config(config_path)
        return "Keep apply", json_path, txt_path, project_path
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import listed media files onto a Premiere sequence, import into a new sequence "
            "in the same project, apply a manual KEEP JSON, copy a source sequence and "
            "KEEP-trim the copy, import and keep-trim in one pass, or apply frame-exact "
            "intrinsic Motion animation, optionally with a sequence-range insert, "
            "to a duplicated sequence."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to import/keep JSON config.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and write a motion plan without creating a Premiere project.",
    )
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
        result = try_run_premiere_import_keep(
            args.config,
            payload,
            dry_run=args.dry_run,
        )
        if result is None:
            mode = payload.get("mode")
            raise ValueError(
                f"Unsupported mode in {args.config}: {mode!r}. "
                "Use import_media, import_to_new_sequence, apply_keep_ranges, "
                "keep_to_new_sequence, import_and_keep, or "
                "premiere_sequence_motion_animation, or "
                "premiere_sequence_insert_from_sequence_and_motion_animation. "
                "Trim review configs belong to main_sequence_trim_review.py."
            )
        label, json_path, txt_path, project_path = result
    except KeyboardInterrupt:
        print(
            "\nImport/keep interrupted by user. Run the same command again to restart.",
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
