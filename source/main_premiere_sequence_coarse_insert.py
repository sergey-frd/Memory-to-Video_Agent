from __future__ import annotations

import argparse
from pathlib import Path

from utils.premiere_sequence_coarse_insert import execute_coarse_insert_stage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute TASK_020 Stage B coarse Family/Nuri insertion."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = execute_coarse_insert_stage(
        args.config.resolve(),
        dry_run_only=args.dry_run,
    )
    for label, path in results.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
