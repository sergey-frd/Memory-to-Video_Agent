from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from utils.sequence_image_export import copy_sequence_images_to_dir


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
            "Collect every still-image source path used by a Premiere project sequence "
            "(images only, never video) and copy the unique images into a destination folder."
        )
    )
    parser.add_argument("--config", type=Path, help="Optional JSON config providing the arguments below.")
    parser.add_argument("--project", type=Path, help="Path to the Premiere .prproj project file.")
    parser.add_argument("--sequence", help="Name of the sequence to scan for images.")
    parser.add_argument("--dest", type=Path, help="Destination folder that should receive the copied images.")
    parser.add_argument(
        "--on-conflict",
        choices=("rename", "overwrite", "skip"),
        default="rename",
        help="What to do when a file with the same name already exists in the destination (default: rename).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied without writing any files.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional path for a JSON manifest of the copy operation.",
    )
    return parser.parse_args()


def _resolve_settings(args: argparse.Namespace) -> dict[str, object]:
    settings: dict[str, object] = {}
    if args.config is not None:
        if not args.config.exists():
            raise FileNotFoundError(f"Config file not found: {args.config}")
        settings = json.loads(args.config.read_text(encoding="utf-8"))

    project = args.project or (Path(settings["project"]) if settings.get("project") else None)
    sequence = args.sequence or settings.get("sequence")
    dest = args.dest or (Path(settings["dest"]) if settings.get("dest") else None)
    on_conflict = args.on_conflict if args.on_conflict != "rename" else settings.get("on_conflict", "rename")
    dry_run = args.dry_run or bool(settings.get("dry_run", False))
    manifest = args.manifest or (Path(settings["manifest"]) if settings.get("manifest") else None)

    if project is None:
        raise ValueError("A project path is required (use --project or the 'project' config key).")
    if not sequence:
        raise ValueError("A sequence name is required (use --sequence or the 'sequence' config key).")
    if dest is None:
        raise ValueError("A destination folder is required (use --dest or the 'dest' config key).")

    return {
        "project": Path(project),
        "sequence": str(sequence),
        "dest": Path(dest),
        "on_conflict": str(on_conflict),
        "dry_run": bool(dry_run),
        "manifest": manifest,
    }


def main() -> int:
    _configure_stdio()
    args = parse_args()
    settings = _resolve_settings(args)

    result = copy_sequence_images_to_dir(
        settings["project"],
        settings["sequence"],
        settings["dest"],
        on_conflict=settings["on_conflict"],
        dry_run=settings["dry_run"],
    )

    print(f"Project:           {result.project_path}")
    print(f"Sequence:          {result.sequence_name}")
    print(f"Destination:       {result.image_dest}")
    print(f"Image references:  {result.image_reference_count}")
    print(f"Unique images:     {result.unique_image_count}")
    copied_count = result.copied_count()
    print(f"Copied images:     {copied_count}{' (dry-run)' if settings['dry_run'] else ''}")
    if result.missing_sources:
        print(f"Missing sources:   {len(result.missing_sources)}")
        for missing in result.missing_sources:
            print(f"  - {missing}")

    manifest_path = settings["manifest"]
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Manifest saved to: {manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
