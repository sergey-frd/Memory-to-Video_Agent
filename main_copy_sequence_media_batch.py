from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from utils.sequence_image_export import (
    MEDIA_KIND_IMAGE,
    MEDIA_KIND_VIDEO,
    run_copy_sequence_media_from_config,
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
        description=(
            "Copy media referenced by Premiere project sequences into destination folders, "
            "driven by a JSON config. Images and videos are routed to separate destinations; "
            "video copying is opt-in via 'copy_videos'."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to the media-copy JSON config file.")
    return parser.parse_args()


def main() -> int:
    _configure_stdio()
    args = parse_args()

    results, manifest_path = run_copy_sequence_media_from_config(args.config)

    print(f"Config: {args.config}")
    for result in results:
        print("-" * 60)
        print(f"Sequence:           {result.sequence_name}")
        if result.image_dest is not None:
            print(f"Image destination:  {result.image_dest}")
            print(f"  Image references: {result.image_reference_count} (unique: {result.unique_image_count})")
            print(f"  Images copied:    {result.copied_count(MEDIA_KIND_IMAGE)}")
        if result.video_dest is not None:
            print(f"Video destination:  {result.video_dest}")
            print(f"  Video references: {result.video_reference_count} (unique: {result.unique_video_count})")
            print(f"  Videos copied:    {result.copied_count(MEDIA_KIND_VIDEO)}")
        if result.missing_sources:
            print(f"  Missing sources:  {len(result.missing_sources)}")
            for missing in result.missing_sources:
                print(f"    - {missing}")

    if manifest_path is not None:
        print("-" * 60)
        print(f"Manifest saved to: {manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
