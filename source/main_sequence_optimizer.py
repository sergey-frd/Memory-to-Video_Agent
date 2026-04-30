from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from config import Settings
from utils.premiere_project import parse_premiere_project_sequence_clips
from utils.premiere_project_export import (
    export_optimized_premiere_project,
    export_optimized_premiere_project_sequence_copy,
)
from utils.premiere_xml_export import export_optimized_premiere_xml
from utils.premiere_xml import parse_premiere_sequence_clips
from utils.sequence_optimizer import format_sequence_report, optimize_sequence
from utils.sequence_structure_report import derive_structure_report_path, write_sequence_structure_report


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
        description="Parse Premiere XML, collect regeneration assets, and build a recommended reordered sequence."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--xml", type=Path, help="Path to the Adobe Premiere XML file.")
    source_group.add_argument("--xml-dir", type=Path, help="Directory with Adobe Premiere XML files for batch processing.")
    source_group.add_argument("--prproj", type=Path, help="Path to the Adobe Premiere project (.prproj) file.")
    parser.add_argument(
        "--regeneration-assets-dir",
        type=Path,
        required=True,
        help="Path to the regeneration_assets directory with scene_analysis and v_prompt files.",
    )
    parser.add_argument(
        "--sequence-name",
        type=str,
        default=None,
        help="Optional Premiere sequence name. If omitted, the sequence with the most .mp4 clips is used.",
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="heuristic",
        choices=("heuristic", "openai"),
        help="Optimization engine. The OpenAI path is currently a stub and falls back to heuristic mode.",
    )
    parser.add_argument("--output-json", type=Path, default=None, help="Optional JSON output path.")
    parser.add_argument("--output-txt", type=Path, default=None, help="Optional text report output path.")
    parser.add_argument(
        "--translation-results",
        type=Path,
        default=None,
        help="Optional path to the matching FCP Translation Results text file. If omitted, the nearest report beside XML is used.",
    )
    parser.add_argument(
        "--batch-pattern",
        type=str,
        default="*.xml",
        help="Glob pattern for batch XML discovery. Used only with --xml-dir.",
    )
    parser.add_argument(
        "--output-xml",
        type=Path,
        default=None,
        help="Optional output Premiere XML path with the optimized sequence order applied.",
    )
    parser.add_argument(
        "--output-prproj",
        type=Path,
        default=None,
        help="Optional output Premiere project (.prproj) path with the optimized sequence order applied.",
    )
    parser.add_argument(
        "--new-sequence-name",
        type=str,
        default=None,
        help="Optional new sequence name for prproj export. If set, the source sequence stays untouched and an optimized clone is added.",
    )
    parser.add_argument(
        "--enable-auto-transitions",
        action="store_true",
        help="Enable auto-generated transitions between contiguous video clips in prproj export.",
    )
    parser.add_argument(
        "--enable-subject-series-grouping",
        action="store_true",
        help="Enable grouping of visually similar subject-series shots.",
    )
    parser.add_argument(
        "--allow-transition-handle-trimming",
        action="store_true",
        help="Allow trimming clip tails to create handles for auto transitions in prproj export.",
    )
    return parser.parse_args()


def run_sequence_optimizer(
    xml_path: Path,
    *,
    regeneration_assets_dir: Path,
    sequence_name: str | None = None,
    engine: str = "heuristic",
    settings: Settings | None = None,
    output_json: Path | None = None,
    output_txt: Path | None = None,
    output_xml: Path | None = None,
    translation_results_path: Path | None = None,
    enable_subject_series_grouping: bool = False,
) -> tuple[Path, Path]:
    settings = settings or Settings()
    settings.ensure_output()

    if not regeneration_assets_dir.exists():
        raise FileNotFoundError(f"regeneration_assets directory not found: {regeneration_assets_dir}")

    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path, sequence_name)
    result = optimize_sequence(
        source_xml=xml_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
        engine=engine,
        translation_results_path=translation_results_path,
        enable_subject_series_grouping=enable_subject_series_grouping,
    )

    file_stem = f"{xml_path.stem}_optimized_sequence"
    json_path = output_json or settings.output_dir / f"{file_stem}.json"
    txt_path = output_txt or settings.output_dir / f"{file_stem}.txt"

    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(format_sequence_report(result), encoding="utf-8")
    write_sequence_structure_report(
        result,
        output_path=derive_structure_report_path(txt_path),
    )
    if output_xml is not None:
        export_optimized_premiere_xml(
            source_xml_path=xml_path,
            optimization_result=result,
            output_xml_path=output_xml,
        )
    return json_path, txt_path


def run_project_sequence_optimizer(
    project_path: Path,
    *,
    regeneration_assets_dir: Path,
    sequence_name: str | None = None,
    engine: str = "heuristic",
    settings: Settings | None = None,
    output_json: Path | None = None,
    output_txt: Path | None = None,
    output_prproj: Path | None = None,
    new_sequence_name: str | None = None,
    translation_results_path: Path | None = None,
    enable_auto_transitions: bool = False,
    enable_subject_series_grouping: bool = False,
    allow_transition_handle_trimming: bool = False,
) -> tuple[Path, Path]:
    settings = settings or Settings()
    settings.ensure_output()

    if not regeneration_assets_dir.exists():
        raise FileNotFoundError(f"regeneration_assets directory not found: {regeneration_assets_dir}")

    selected_sequence_name, clips = parse_premiere_project_sequence_clips(project_path, sequence_name)
    result = optimize_sequence(
        source_xml=project_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
        engine=engine,
        translation_results_path=translation_results_path,
        enable_subject_series_grouping=enable_subject_series_grouping,
    )

    file_stem = f"{project_path.stem}_optimized_sequence"
    json_path = output_json or settings.output_dir / f"{file_stem}.json"
    txt_path = output_txt or settings.output_dir / f"{file_stem}.txt"

    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(format_sequence_report(result), encoding="utf-8")
    write_sequence_structure_report(
        result,
        output_path=derive_structure_report_path(txt_path),
    )
    if output_prproj is not None:
        if new_sequence_name:
            export_optimized_premiere_project_sequence_copy(
                source_project_path=project_path,
                optimization_result=result,
                output_project_path=output_prproj,
                new_sequence_name=new_sequence_name,
                enable_auto_transitions=enable_auto_transitions,
                allow_transition_handle_trimming=allow_transition_handle_trimming,
            )
        else:
            export_optimized_premiere_project(
                source_project_path=project_path,
                optimization_result=result,
                output_project_path=output_prproj,
                enable_auto_transitions=enable_auto_transitions,
                allow_transition_handle_trimming=allow_transition_handle_trimming,
            )
    return json_path, txt_path


def run_sequence_optimizer_batch(
    xml_dir: Path,
    *,
    regeneration_assets_dir: Path,
    sequence_name: str | None = None,
    engine: str = "heuristic",
    settings: Settings | None = None,
    output_json: Path | None = None,
    output_txt: Path | None = None,
    batch_pattern: str = "*.xml",
) -> tuple[Path, Path]:
    settings = settings or Settings()
    settings.ensure_output()

    if not xml_dir.exists():
        raise FileNotFoundError(f"XML directory not found: {xml_dir}")
    if not xml_dir.is_dir():
        raise NotADirectoryError(f"Expected XML directory, got file: {xml_dir}")

    xml_paths = sorted(path for path in xml_dir.glob(batch_pattern) if path.is_file())
    if not xml_paths:
        raise FileNotFoundError(f"No XML files found in {xml_dir} using pattern '{batch_pattern}'.")

    batch_items: list[dict[str, object]] = []
    for xml_path in xml_paths:
        try:
            json_path, txt_path = run_sequence_optimizer(
                xml_path,
                regeneration_assets_dir=regeneration_assets_dir,
                sequence_name=sequence_name,
                engine=engine,
                settings=settings,
            )
            result_payload = json.loads(json_path.read_text(encoding="utf-8"))
            batch_items.append(
                {
                    "xml_path": str(xml_path),
                    "status": "ok",
                    "selected_sequence_name": result_payload.get("selected_sequence_name"),
                    "clip_count": len(result_payload.get("entries", [])),
                    "translation_report_path": result_payload.get("translation_report_path"),
                    "lost_effect_clip_count": len(result_payload.get("clips_with_lost_effects", [])),
                    "report_json": str(json_path),
                    "report_txt": str(txt_path),
                }
            )
        except Exception as exc:
            batch_items.append(
                {
                    "xml_path": str(xml_path),
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    summary_payload = {
        "xml_dir": str(xml_dir),
        "batch_pattern": batch_pattern,
        "engine_requested": engine,
        "processed": len(batch_items),
        "succeeded": sum(1 for item in batch_items if item["status"] == "ok"),
        "failed": sum(1 for item in batch_items if item["status"] == "error"),
        "items": batch_items,
    }

    file_stem = f"{xml_dir.name}_optimized_sequence_batch"
    json_path = output_json or settings.output_dir / f"{file_stem}.json"
    txt_path = output_txt or settings.output_dir / f"{file_stem}.txt"
    json_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(_format_batch_report(summary_payload), encoding="utf-8")
    return json_path, txt_path


def _format_batch_report(summary_payload: dict[str, object]) -> str:
    items = summary_payload.get("items", [])
    successful_items = [item for item in items if isinstance(item, dict) and item.get("status") == "ok"]
    failed_items = [item for item in items if isinstance(item, dict) and item.get("status") == "error"]

    lines = [
        "SEQUENCE OPTIMIZATION BATCH REPORT",
        "",
        f"XML directory: {summary_payload.get('xml_dir')}",
        f"Pattern: {summary_payload.get('batch_pattern')}",
        f"Engine requested: {summary_payload.get('engine_requested')}",
        f"Processed: {summary_payload.get('processed')}",
        f"Succeeded: {summary_payload.get('succeeded')}",
        f"Failed: {summary_payload.get('failed')}",
        "",
        "Successful runs",
        "",
    ]

    if successful_items:
        for item in successful_items:
            lines.extend(
                [
                    f"- {Path(str(item.get('xml_path'))).name}",
                    f"  Sequence: {item.get('selected_sequence_name')}",
                    f"  Clips: {item.get('clip_count')}",
                    f"  Lost effect clips: {item.get('lost_effect_clip_count')}",
                    f"  Translation report: {item.get('translation_report_path') or '<not found>'}",
                    f"  JSON: {item.get('report_json')}",
                    f"  TXT: {item.get('report_txt')}",
                    "",
                ]
            )
    else:
        lines.extend(["- none", ""])

    lines.extend(["Failed runs", ""])
    if failed_items:
        for item in failed_items:
            lines.extend(
                [
                    f"- {Path(str(item.get('xml_path'))).name}",
                    f"  {item.get('error_type')}: {item.get('error')}",
                    "",
                ]
            )
    else:
        lines.extend(["- none", ""])

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    _configure_stdio()
    args = parse_args()
    if args.xml_dir is not None:
        json_path, txt_path = run_sequence_optimizer_batch(
            args.xml_dir,
            regeneration_assets_dir=args.regeneration_assets_dir,
            sequence_name=args.sequence_name,
            engine=args.engine,
            output_json=args.output_json,
            output_txt=args.output_txt,
            batch_pattern=args.batch_pattern,
        )
        print(f"Batch sequence optimization JSON saved to: {json_path}")
        print(f"Batch sequence optimization text report saved to: {txt_path}")
        return

    if args.prproj is not None:
        json_path, txt_path = run_project_sequence_optimizer(
            args.prproj,
            regeneration_assets_dir=args.regeneration_assets_dir,
            sequence_name=args.sequence_name,
            engine=args.engine,
            output_json=args.output_json,
            output_txt=args.output_txt,
            output_prproj=args.output_prproj,
            new_sequence_name=args.new_sequence_name,
            translation_results_path=args.translation_results,
            enable_auto_transitions=args.enable_auto_transitions,
            enable_subject_series_grouping=args.enable_subject_series_grouping,
            allow_transition_handle_trimming=args.allow_transition_handle_trimming,
        )
    else:
        json_path, txt_path = run_sequence_optimizer(
            args.xml,
            regeneration_assets_dir=args.regeneration_assets_dir,
            sequence_name=args.sequence_name,
            engine=args.engine,
            output_json=args.output_json,
            output_txt=args.output_txt,
            output_xml=args.output_xml,
            translation_results_path=args.translation_results,
            enable_subject_series_grouping=args.enable_subject_series_grouping,
        )
    print(f"Sequence optimization JSON saved to: {json_path}")
    print(f"Sequence optimization text report saved to: {txt_path}")
    print(f"Sequence structure report saved to: {derive_structure_report_path(txt_path)}")
    if args.output_xml is not None:
        print(f"Optimized Premiere XML saved to: {args.output_xml}")
    if args.output_prproj is not None:
        print(f"Optimized Premiere project saved to: {args.output_prproj}")


if __name__ == "__main__":
    main()
