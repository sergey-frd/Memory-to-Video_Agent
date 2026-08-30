from __future__ import annotations

import argparse
from pathlib import Path

from utils.premiere_sequence_ripple_delete import execute_ripple_delete_task


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute saved-project-verified Premiere ripple deletes."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = execute_ripple_delete_task(
        args.config.resolve(),
        dry_run_only=args.dry_run,
    )
    for label, path in results.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
