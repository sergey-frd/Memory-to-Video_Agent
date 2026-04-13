from __future__ import annotations

import argparse
import os
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from api.openai_image import edit_image_with_openai
from config import Settings
from utils.image_analysis import analyze_image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a prompt from input/ to an image from input/ and save the result to output/."
    )
    parser.add_argument(
        "--image",
        "-i",
        type=str,
        required=True,
        help="Image filename inside input/, for example image.png.",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        type=str,
        required=True,
        help="Prompt filename inside input/, for example prompt.txt.",
    )
    parser.add_argument(
        "--stage-id",
        "-s",
        type=str,
        default=None,
        help="Optional stage id written to logs.",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default="gpt-image-1.5",
        help="OpenAI Images edit model, for example gpt-image-1.5.",
    )
    return parser.parse_args()


def load_prompt(prompt_path: Path) -> str:
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file was not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def resolve_input_path(base_dir: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate


def hash_file(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def measure_visual_difference(source_path: Path, output_path: Path) -> tuple[list[float], list[float], list[tuple[int, int]]]:
    with Image.open(source_path).convert("RGB") as source_image, Image.open(output_path).convert("RGB") as output_image:
        diff = ImageChops.difference(source_image, output_image)
        stat = ImageStat.Stat(diff)
    return stat.mean, stat.rms, stat.extrema


def main() -> None:
    args = parse_args()
    os.environ["OPENAI_IMAGE_MODEL"] = args.model

    settings = Settings()
    settings.ensure_output()

    image_path = resolve_input_path(settings.input_dir, args.image)
    prompt_path = resolve_input_path(settings.input_dir, args.prompt)
    if not image_path.exists():
        raise FileNotFoundError(f"Source image was not found: {image_path}")

    prompt_text = load_prompt(prompt_path)
    metadata = analyze_image(image_path)
    output_image = settings.output_dir / f"{image_path.stem}_final.png"

    edit_image_with_openai(
        image_path=image_path,
        style="final-frame",
        output_path=output_image,
        metadata=metadata,
        stage_id=args.stage_id or "main1",
        prompt_override=prompt_text,
    )

    prompt_log_path = settings.output_dir / f"{image_path.stem}_final_prompt.txt"
    prompt_log_path.write_text(
        f"Stage: {args.stage_id or 'main1'}\nModel: {args.model}\n\n{prompt_text}",
        encoding="utf-8",
    )

    src_hash = hash_file(image_path)
    dst_hash = hash_file(output_image)
    print(f"Source image hash: {src_hash}")
    print(f"Final image hash: {dst_hash}")
    if src_hash == dst_hash:
        print("WARNING: output image is byte-identical to the input image.")

    mean_diff, rms_diff, extrema = measure_visual_difference(image_path, output_image)
    print(f"Mean RGB diff: {mean_diff}")
    print(f"RMS RGB diff: {rms_diff}")
    print(f"Max RGB diff: {[high for _, high in extrema]}")
    if max(rms_diff) < 2.0:
        raise RuntimeError(
            "Fail-fast: output image is visually near-identical to the input image. "
            f"Mean RGB diff={mean_diff}, RMS RGB diff={rms_diff}, Max RGB diff={[high for _, high in extrema]}."
        )

    print(f"Final frame saved to: {output_image}")


if __name__ == "__main__":
    main()
