from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from utils.sequence_keep_apply import run_sequence_keep_apply_from_config
from utils.sequence_media_import import run_sequence_media_import_from_config


_COMBINED_MODES = {"import_and_keep", "import_keep", "import_and_apply_keep"}


def is_import_and_keep_config(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    mode = str(payload.get("mode") or "").strip().casefold()
    if mode in _COMBINED_MODES:
        return True
    return _has_import_sources(payload) and _has_keep_sources(payload)


def _has_import_sources(payload: dict[str, object]) -> bool:
    if str(payload.get("import_path") or "").strip():
        return True
    items = payload.get("items")
    if isinstance(items, list) and any(
        isinstance(item, dict) and str(item.get("source_path") or "").strip() for item in items
    ):
        return True
    return bool(str(payload.get("root_directory") or "").strip()) and isinstance(payload.get("files"), list)


def _has_keep_sources(payload: dict[str, object]) -> bool:
    if str(payload.get("keep_ranges_path") or payload.get("keep_path") or "").strip():
        return True
    return isinstance(payload.get("operations"), list) or isinstance(payload.get("clips"), list)


def run_sequence_import_and_keep_from_config(config_path: Path) -> tuple[Path, Path, Path | None]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Import-and-keep config must be a JSON object: {config_path}")
    if not _has_import_sources(payload) and str(payload.get("mode") or "").strip().casefold() in _COMBINED_MODES:
        raise ValueError("Import-and-keep config needs 'import_path', inline 'items', or 'files'.")
    if not _has_keep_sources(payload) and str(payload.get("mode") or "").strip().casefold() in _COMBINED_MODES:
        raise ValueError(
            "Import-and-keep config needs 'keep_ranges_path', inline 'operations', or inline 'clips'."
        )

    import_job_path = _resolve_optional_file(payload.get("import_path"), config_path, label="import_path")
    keep_job_path = _resolve_optional_file(
        payload.get("keep_ranges_path") or payload.get("keep_path"),
        config_path,
        label="keep_ranges_path",
    )
    import_job = _load_json_object(import_job_path) if import_job_path is not None else payload
    keep_job = _load_json_object(keep_job_path) if keep_job_path is not None else payload

    source_project = _resolve_project_path(
        payload.get("project_path") or import_job.get("project_path"),
        label="project_path",
    )
    sequence_name = _first_non_empty_text(
        payload.get("sequence_name"),
        payload.get("source_sequence_name"),
        import_job.get("sequence_name"),
        import_job.get("source_sequence_name"),
        keep_job.get("sequence_name"),
        keep_job.get("source_sequence_name"),
    )
    reports_dir = Path(
        str(payload.get("reports_dir") or (source_project.parent / "import_keep_reports"))
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    import_output_path = Path(
        str(
            payload.get("import_output_project_path")
            or payload.get("import_project_path")
            or (source_project.parent / f"{source_project.stem}_import.prproj")
        )
    )
    keep_output_path = Path(
        str(
            payload.get("output_project_path")
            or keep_job.get("output_project_path")
            or (source_project.parent / f"{source_project.stem}_keep.prproj")
        )
    )
    ripple_compact = bool(payload.get("ripple_compact", keep_job.get("ripple_compact", True)))
    write_project = bool(payload.get("write_project", True))

    progress = _build_progress_reporter(reports_dir / "sequence_import_and_keep_progress.log")
    progress(f"Import-and-keep started. Config: {config_path}")
    progress(f"Source project: {source_project}")
    progress(f"Import output: {import_output_path}")
    progress(f"Keep output: {keep_output_path}")

    import_step_path = reports_dir / "import_and_keep_import_step.json"
    keep_step_path = reports_dir / "import_and_keep_keep_step.json"
    import_step_path.write_text(
        json.dumps(
            _build_import_step(
                payload=payload,
                import_job=import_job,
                import_job_path=import_job_path,
                source_project=source_project,
                sequence_name=sequence_name,
                import_output_path=import_output_path,
                reports_dir=reports_dir,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    keep_step_path.write_text(
        json.dumps(
            _build_keep_step(
                payload=payload,
                keep_job=keep_job,
                keep_job_path=keep_job_path,
                sequence_name=sequence_name,
                import_output_path=import_output_path,
                keep_output_path=keep_output_path,
                reports_dir=reports_dir,
                ripple_compact=ripple_compact,
                write_project=write_project,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    import_json, import_txt, imported_project = run_sequence_media_import_from_config(import_step_path)
    if imported_project is None:
        raise ValueError("Import-and-keep stopped: media import did not write a project.")
    progress(f"Import completed: {imported_project}")

    keep_json, keep_txt, kept_project = run_sequence_keep_apply_from_config(keep_step_path)
    progress(f"Keep-apply completed: {kept_project}")

    report_payload = {
        "mode": "import_and_keep",
        "source_project_path": str(source_project),
        "import_path": str(import_job_path) if import_job_path is not None else None,
        "keep_ranges_path": str(keep_job_path) if keep_job_path is not None else None,
        "sequence_name": sequence_name or None,
        "import_output_project_path": str(imported_project),
        "output_project_path": str(kept_project) if kept_project is not None else None,
        "ripple_compact": ripple_compact,
        "import_report_json": str(import_json),
        "import_report_txt": str(import_txt),
        "keep_report_json": str(keep_json),
        "keep_report_txt": str(keep_txt),
    }
    json_path = reports_dir / f"{source_project.stem}_import_and_keep.json"
    txt_path = reports_dir / f"{source_project.stem}_import_and_keep.txt"
    json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(_build_combined_report(report_payload), encoding="utf-8")
    progress(f"Import-and-keep completed. Report: {json_path}")
    return json_path, txt_path, kept_project


def _build_import_step(
    *,
    payload: dict[str, object],
    import_job: dict[str, object],
    import_job_path: Path | None,
    source_project: Path,
    sequence_name: str,
    import_output_path: Path,
    reports_dir: Path,
) -> dict[str, object]:
    step: dict[str, object] = {
        "mode": "import_media",
        "project_path": str(source_project),
        "sequence_name": sequence_name,
        "create_sequence_if_missing": bool(
            payload.get(
                "create_sequence_if_missing",
                import_job.get("create_sequence_if_missing", True),
            )
        ),
        "still_duration_seconds": float(
            payload.get("still_duration_seconds")
            or import_job.get("still_duration_seconds")
            or 5
        ),
        "output_project_path": str(import_output_path),
        "reports_dir": str(reports_dir),
        "write_project": True,
    }
    template_raw = payload.get("template_project_path") or import_job.get("template_project_path")
    if str(template_raw or "").strip():
        step["template_project_path"] = str(template_raw)
    if import_job_path is not None:
        step["import_path"] = str(import_job_path)
        return step
    if import_job.get("items"):
        step["items"] = import_job["items"]
    if import_job.get("files"):
        step["files"] = import_job["files"]
    if import_job.get("root_directory"):
        step["root_directory"] = import_job["root_directory"]
    return step


def _build_keep_step(
    *,
    payload: dict[str, object],
    keep_job: dict[str, object],
    keep_job_path: Path | None,
    sequence_name: str,
    import_output_path: Path,
    keep_output_path: Path,
    reports_dir: Path,
    ripple_compact: bool,
    write_project: bool,
) -> dict[str, object]:
    step: dict[str, object] = {
        "mode": "apply_keep_ranges",
        "project_path": str(import_output_path),
        "sequence_name": sequence_name,
        "output_project_path": str(keep_output_path),
        "reports_dir": str(reports_dir),
        "ripple_compact": ripple_compact,
        "write_project": write_project,
    }
    if keep_job_path is not None:
        step["keep_ranges_path"] = str(keep_job_path)
        return step
    if keep_job.get("operations"):
        step["operations"] = keep_job["operations"]
    if keep_job.get("clips"):
        step["clips"] = keep_job["clips"]
    return step


def _build_combined_report(payload: dict[str, object]) -> str:
    lines = [
        "Sequence Import and Keep",
        "=" * 72,
        f"Source project: {payload.get('source_project_path')}",
        f"Import project: {payload.get('import_output_project_path')}",
        f"Keep project: {payload.get('output_project_path')}",
        f"Sequence: {payload.get('sequence_name')}",
        f"Ripple compact: {payload.get('ripple_compact')}",
        "",
        "Step reports:",
        "-" * 72,
        f"Import JSON: {payload.get('import_report_json')}",
        f"Keep JSON: {payload.get('keep_report_json')}",
        "",
        "How to use in Premiere:",
        "-" * 72,
        "1. Open the keep output .prproj. The original project file was not modified.",
        "2. The intermediate *_import.prproj is the untrimmed import copy.",
        "3. Listed videos keep only the specified source ranges; stills use duration.",
        "",
    ]
    return "\n".join(lines)


def _resolve_optional_file(value: object, config_path: Path, *, label: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_file() and not path.is_absolute():
        path = (config_path.parent / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} was not found: {path}")
    return path


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def _resolve_project_path(value: object, *, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Import-and-keep config must contain '{label}'.")
    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Premiere project file not found: {path}")
    return path


def _first_non_empty_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_progress_reporter(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def progress(message: str) -> None:
        line = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return progress
