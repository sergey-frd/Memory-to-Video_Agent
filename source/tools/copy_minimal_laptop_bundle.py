#!/usr/bin/env python3
"""Copy a minimal watercolor batch bundle to a target laptop directory."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = Path(r"<LOCAL_PATH>")
BASELINE_FILE = "env_baseline_chatgpt_watercolor.json"

REQUIRED_FILES = (
    "main_chatgpt_portrait_batch.py",
    "config.py",
    "requirements.txt",
    "chatgpt_watercolor_on_paper_config.json",
    "chatgpt_all_styles_config.json",
    "chatgpt_portrait_base_config.json",
    "chatgpt_artistic_photo_portret_config.json",
    "config_Ziggi.json",
    "run_chatgpt_portrait_batch_existing.bat",
    "run_chatgpt_watercolor_on_paper_existing.bat",
    "run_chatgpt_style_batch_existing.bat",
    "run_chatgpt_style_menu_existing.bat",
    "run_chatgpt_artistic_photo_portret_existing.bat",
    "run_laptop_env_snapshot.bat",
    "run_laptop_env_compare.bat",
)

REQUIRED_DIRS = (
    "api",
    "utils",
    "models",
    "tools",
)

ENSURE_DIRS = (
    "input",
    "output",
)


def _copy_file(source_root: Path, target_root: Path, rel_path: str) -> str:
    src = source_root / rel_path
    dst = target_root / rel_path
    if not src.exists():
        raise FileNotFoundError(f"Missing source file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return rel_path


def _copy_dir(source_root: Path, target_root: Path, rel_path: str) -> str:
    src = source_root / rel_path
    dst = target_root / rel_path
    if not src.exists():
        raise FileNotFoundError(f"Missing source directory: {src}")
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache"),
    )
    return rel_path


def _run_env_checks(source_root: Path, target_root: Path, strict_versions: bool) -> int:
    checker = source_root / "tools" / "check_laptop_watercolor_env.py"
    baseline_path = source_root / BASELINE_FILE
    report_path = target_root / "env_compare_report_chatgpt_watercolor.json"
    python_exe = sys.executable

    snapshot_cmd = [
        python_exe,
        str(checker),
        "--project-root",
        str(source_root),
        "snapshot",
        "--output",
        str(baseline_path),
    ]
    compare_cmd = [
        python_exe,
        str(checker),
        "--project-root",
        str(target_root),
        "compare",
        "--baseline",
        str(baseline_path),
        "--report",
        str(report_path),
    ]
    if strict_versions:
        compare_cmd.append("--strict-versions")

    print("[INFO] Running snapshot from source project...")
    snap = subprocess.run(snapshot_cmd, check=False)
    if snap.returncode != 0:
        return snap.returncode

    print("[INFO] Running compare against target project...")
    cmp_result = subprocess.run(compare_cmd, check=False)
    return cmp_result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy minimal files needed for watercolor batch to a laptop directory."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT,
        help="Source project root (default: current repository root).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=r"Target project root (default: <LOCAL_PATH>
    )
    parser.add_argument(
        "--run-compare",
        action="store_true",
        help="Run snapshot+compare after copying.",
    )
    parser.add_argument(
        "--strict-versions",
        action="store_true",
        help="When --run-compare is set, enforce requirements version constraints.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without copying.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source.resolve()
    target_root = args.target.resolve()

    copied_files: list[str] = []
    copied_dirs: list[str] = []

    print(f"[INFO] Source: {source_root}")
    print(f"[INFO] Target: {target_root}")

    if not args.dry_run:
        target_root.mkdir(parents=True, exist_ok=True)

    for rel in ENSURE_DIRS:
        ensure_path = target_root / rel
        if args.dry_run:
            print(f"[DRY-RUN] ensure dir: {ensure_path}")
        else:
            ensure_path.mkdir(parents=True, exist_ok=True)

    for rel in REQUIRED_FILES:
        if args.dry_run:
            print(f"[DRY-RUN] copy file: {rel}")
            continue
        copied_files.append(_copy_file(source_root, target_root, rel))

    for rel in REQUIRED_DIRS:
        if args.dry_run:
            print(f"[DRY-RUN] copy dir: {rel}")
            continue
        copied_dirs.append(_copy_dir(source_root, target_root, rel))

    manifest = {
        "copied_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_root": str(source_root),
        "target_root": str(target_root),
        "required_files": list(REQUIRED_FILES),
        "required_dirs": list(REQUIRED_DIRS),
        "ensured_dirs": list(ENSURE_DIRS),
        "copied_files_count": len(copied_files),
        "copied_dirs_count": len(copied_dirs),
    }

    manifest_path = target_root / "minimal_bundle_manifest.json"
    if args.dry_run:
        print("[DRY-RUN] manifest would be written:", manifest_path)
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] Copied minimal bundle. Manifest: {manifest_path}")

    if args.run_compare and not args.dry_run:
        exit_code = _run_env_checks(source_root, target_root, args.strict_versions)
        if exit_code != 0:
            print("[FAIL] Compare check failed after copy.")
            return exit_code
        print("[OK] Compare check passed after copy.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
