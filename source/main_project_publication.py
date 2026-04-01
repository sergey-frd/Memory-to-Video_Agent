from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from utils.project_publication import write_publication_bundle


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
        description="Generate and refresh the publication bundle for the external project-information repository."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("."),
        help="Source project root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("project_publication") / "Memory-to-Video_Agent",
        help="Target directory to refresh. Can be a local clone of the external repository.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Optional explicit path to project_structure_registry.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    result = write_publication_bundle(
        source_root=args.source_root,
        target_dir=args.target_dir,
        registry_path=args.registry,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"Publication bundle refreshed: {result.target_dir}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Snapshot: {result.snapshot_path}")
    print(f"Managed files: {len(result.written_files)}")


if __name__ == "__main__":
    main()
