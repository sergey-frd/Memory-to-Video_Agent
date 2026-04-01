from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from utils.project_sequence_batch import run_project_sequence_batch_from_config


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
        description="Run a batch of Premiere project sequence optimizations from a JSON config file."
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to the batch JSON config file.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    summary_json_path, summary_txt_path = run_project_sequence_batch_from_config(args.config)
    print(f"Project sequence batch JSON saved to: {summary_json_path}")
    print(f"Project sequence batch text report saved to: {summary_txt_path}")


if __name__ == "__main__":
    main()
