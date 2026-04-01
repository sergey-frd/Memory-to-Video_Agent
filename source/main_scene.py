from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path
from typing import Callable

from api.openai_scene import analyze_scene_with_openai, format_scene_report
from config import Settings
from models.scene_analysis import SceneAnalysis


SceneAnalyzer = Callable[[Path, str | None], SceneAnalysis]


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
        description="Проанализировать один кадр и сохранить структурированное описание людей, одежды, фона, позы и настроения."
    )
    parser.add_argument("--image", "-i", type=Path, required=True, help="Путь к исходному изображению.")
    parser.add_argument("--stage-id", "-s", type=str, default=None, help="Необязательный идентификатор этапа для выходных файлов.")
    parser.add_argument("--model", "-m", type=str, default=None, help="Необязательное переопределение модели OpenAI для анализа сцены.")
    parser.add_argument("--output-json", type=Path, default=None, help="Необязательный путь для выходного JSON.")
    parser.add_argument("--output-txt", type=Path, default=None, help="Необязательный путь для текстового отчёта.")
    return parser.parse_args()


def run_scene_analysis(
    image_path: Path,
    *,
    stage_id: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
    analyzer: SceneAnalyzer = analyze_scene_with_openai,
    output_json: Path | None = None,
    output_txt: Path | None = None,
) -> tuple[Path, Path]:
    settings = settings or Settings()
    settings.ensure_output()

    if not image_path.exists():
        raise FileNotFoundError(f"Исходное изображение не найдено: {image_path}")

    resolved_stage = stage_id or image_path.stem
    analysis = analyzer(image_path, model)

    json_path = output_json or settings.output_dir / f"{resolved_stage}_scene_analysis.json"
    txt_path = output_txt or settings.output_dir / f"{resolved_stage}_scene_report.txt"

    json_path.write_text(json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(format_scene_report(analysis), encoding="utf-8")
    return json_path, txt_path


def main() -> None:
    _configure_stdio()
    args = parse_args()
    json_path, txt_path = run_scene_analysis(
        args.image,
        stage_id=args.stage_id,
        model=args.model,
        output_json=args.output_json,
        output_txt=args.output_txt,
    )
    print(f"Scene analysis JSON saved to: {json_path}")
    print(f"JSON анализа сцены сохранён: {json_path}")
    print(f"Текстовый отчёт сохранён: {txt_path}")


if __name__ == "__main__":
    main()
