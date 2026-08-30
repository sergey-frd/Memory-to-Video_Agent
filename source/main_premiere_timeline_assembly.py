from __future__ import annotations

import argparse
from pathlib import Path

from utils.premiere_sequence_timeline_assembly import execute_timeline_assembly


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble a frame-exact video-only Premiere sequence from ranges of "
            "existing in-project sequences."
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = execute_timeline_assembly(
        args.config.resolve(),
        dry_run_only=args.dry_run,
    )
    for label, path in results.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
