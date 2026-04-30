from __future__ import annotations

import argparse
import ctypes
import sys
from datetime import datetime
from pathlib import Path

from config import Settings
from utils.artifact_cleanup import (
    derive_cleanup_report_paths,
    discover_cleanup_candidates,
    execute_cleanup,
    write_cleanup_report,
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
        description="Safely discover and optionally remove stale temporary artifacts from the workspace and reports folders."
    )
    parser.add_argument(
        "--reports-dir",
        action="append",
        default=[],
        help="Optional reports directory to scan for temp_projects and sibling staging folders. Can be passed multiple times.",
    )
    parser.add_argument(
        "--older-than-days",
        type=float,
        default=None,
        help="Only include candidates older than this many days.",
    )
    parser.add_argument(
        "--include-output-build-dirs",
        action="store_true",
        help="Also include legacy build directories inside workspace output.",
    )
    parser.add_argument(
        "--include-output-files",
        action="store_true",
        help="Also include top-level generated artifacts inside workspace output.",
    )
    parser.add_argument(
        "--include-test-runtime-items",
        action="store_true",
        help="Also include top-level test_runtime artifacts inside the workspace.",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Optional directory where candidates are copied before deletion.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the discovered candidates. Without this flag the command runs in dry-run mode.",
    )
    parser.add_argument("--output-json", type=Path, default=None, help="Optional path for the cleanup JSON report.")
    parser.add_argument("--output-txt", type=Path, default=None, help="Optional path for the cleanup text report.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    settings = Settings()
    settings.ensure_output()

    candidates = discover_cleanup_candidates(
        settings=settings,
        extra_reports_dirs=[Path(item) for item in args.reports_dir],
        older_than_days=args.older_than_days,
        include_output_build_dirs=args.include_output_build_dirs,
        include_output_files=args.include_output_files,
        include_test_runtime_items=args.include_test_runtime_items,
    )
    summary = execute_cleanup(
        candidates,
        project_root=settings.project_root,
        archive_dir=args.archive_dir,
        dry_run=not args.execute,
        older_than_days=args.older_than_days,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_json, output_txt = (
        args.output_json,
        args.output_txt,
    )
    if output_json is None or output_txt is None:
        default_json, default_txt = derive_cleanup_report_paths(settings, timestamp=timestamp)
        output_json = output_json or default_json
        output_txt = output_txt or default_txt

    write_cleanup_report(summary, output_json=output_json, output_txt=output_txt)
    print(f"Cleanup candidates: {len(summary.candidates)}")
    print(f"Dry run: {summary.dry_run}")
    print(f"Cleanup JSON report saved to: {output_json}")
    print(f"Cleanup text report saved to: {output_txt}")
    if args.execute:
        print(f"Deleted paths: {len(summary.deleted_paths)}")
    if args.archive_dir is not None:
        print(f"Archive dir: {args.archive_dir}")


if __name__ == "__main__":
    main()
