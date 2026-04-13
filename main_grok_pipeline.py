from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.grok_video import GrokVideoRequest, generate_video_with_grok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate videos in Grok from a desktop-pipeline manifest."
    )
    parser.add_argument(
        "--manifest",
        "-m",
        type=Path,
        required=True,
        help="Path to the desktop pipeline manifest JSON.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="grok-video",
        help="xAI video model name.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=None,
        help="Optional target video duration.",
    )
    parser.add_argument(
        "--aspect-ratio",
        type=str,
        default=None,
        help="Optional aspect ratio, for example 9:16.",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        default=None,
        help="Optional resolution hint, for example 720p or 1080p.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600.0,
        help="Maximum time to wait for each video job.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Manifest file was not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def run_pipeline(args: argparse.Namespace) -> Path:
    manifest = load_manifest(args.manifest)
    stage_id = manifest["stage_id"]
    initial_image = Path(manifest["initial_image"])
    steps = manifest["steps"]
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("Manifest does not contain any steps.")

    manifest_dir = args.manifest.parent
    current_input_image = initial_image
    output_steps: list[dict[str, str | int]] = []

    for step in steps:
        step_index = int(step["index"])
        video_prompt_file = Path(step["video_prompt_file"])
        if not video_prompt_file.exists():
            raise FileNotFoundError(f"Video prompt file was not found: {video_prompt_file}")
        output_video = manifest_dir / f"{stage_id}_video_{step_index}.mp4"
        prompt_text = video_prompt_file.read_text(encoding="utf-8")

        saved_path = generate_video_with_grok(
            GrokVideoRequest(
                prompt=prompt_text,
                image_path=current_input_image,
                output_path=output_video,
                model=args.model,
                duration_seconds=args.duration_seconds,
                aspect_ratio=args.aspect_ratio,
                resolution=args.resolution,
                timeout_seconds=args.timeout_seconds,
            )
        )

        output_steps.append(
            {
                "index": step_index,
                "input_image": str(current_input_image),
                "video_prompt_file": str(video_prompt_file),
                "output_video": str(saved_path),
            }
        )
        current_input_image = Path(step["final_frame_image"])

    output_manifest = {
        "stage_id": stage_id,
        "source_manifest": str(args.manifest),
        "model": args.model,
        "steps": output_steps,
    }
    output_manifest_path = manifest_dir / f"{stage_id}_grok_videos_manifest.json"
    output_manifest_path.write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_manifest_path


def main() -> None:
    args = parse_args()
    output_manifest_path = run_pipeline(args)
    print(f"Grok video pipeline completed. Manifest saved to: {output_manifest_path}")


if __name__ == "__main__":
    main()
