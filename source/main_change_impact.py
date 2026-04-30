from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from utils.change_impact import (
    available_change_types,
    build_impact_report,
    load_change_registry,
    render_text_report,
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
        description="Read the project change-impact registry and suggest files, tests, and documents to review."
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("project_structure_registry.json"),
        help="Path to the change-impact registry JSON.",
    )
    parser.add_argument(
        "--change-type",
        action="append",
        dest="change_types",
        default=[],
        help="Explicit change type id. Can be passed more than once.",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        dest="changed_files",
        default=[],
        help="Changed file path. Can be passed more than once.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the report as JSON.",
    )
    parser.add_argument(
        "--list-change-types",
        action="store_true",
        help="List all available change types from the registry and exit.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    registry = load_change_registry(args.registry)

    if args.list_change_types:
        for change_type_id in available_change_types(registry):
            print(change_type_id)
        return

    report = build_impact_report(
        args.registry,
        change_type_ids=list(args.change_types),
        changed_files=list(args.changed_files),
        project_root=args.registry.resolve().parent,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    print(render_text_report(report))


if __name__ == "__main__":
    main()
