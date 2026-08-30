from __future__ import annotations

import argparse
from pathlib import Path

from utils.premiere_sequence_delete_only import execute_delete_only_stage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute a saved-project-verified Premiere deletion-only stage."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = execute_delete_only_stage(
        args.config.resolve(),
        dry_run_only=args.dry_run,
    )
    for label, path in results.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
