from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from models.sequence_media_import import ImportFileRequest, MediaImportItem
from utils.premiere_media_import_export import export_media_import_premiere_project
from utils.premiere_project import (
    PremiereProjectError,
    is_supported_image_media_path,
    is_supported_visual_media_path,
    list_named_project_sequence_names,
    load_premiere_project_root,
)
from utils.video_frame_extract import resolve_ffmpeg_executable


_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_DEFAULT_STILL_SECONDS = 5.0
_DEFAULT_VIDEO_SECONDS = 5.0
_IMPORT_MODES = {"import_media", "import_to_new_sequence"}


def is_media_import_config(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    mode = str(payload.get("mode") or "").strip().casefold()
    if mode in _IMPORT_MODES:
        return True
    if isinstance(payload.get("items"), list) and any(
        isinstance(item, dict)
        and (
            _item_source_path_text(item)
            or str(item.get("source_name") or item.get("file") or item.get("filename") or "").strip()
        )
        for item in payload["items"]
    ):
        return True
    if payload.get("root_search_paths") and (
        isinstance(payload.get("files"), list) or isinstance(payload.get("items"), list)
    ):
        return True
    return bool(payload.get("root_directory")) and isinstance(payload.get("files"), list)


def run_sequence_media_import_from_config(config_path: Path) -> tuple[Path, Path, Path | None]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Media-import config must be a JSON object: {config_path}")

    job_payload, import_path = _load_import_job(payload, config_path)
    project_path = _resolve_existing_path(
        job_payload.get("project_path") or payload.get("project_path"),
        label="project_path",
    )
    root_directory, search_roots = _load_search_roots(job_payload, payload)
    mode = str(payload.get("mode") or job_payload.get("mode") or "import_media").strip().casefold()
    if mode not in _IMPORT_MODES:
        has_output_sequence = bool(
            str(payload.get("output_sequence_name") or job_payload.get("output_sequence_name") or "").strip()
        )
        mode = "import_to_new_sequence" if has_output_sequence else "import_media"
    to_new_sequence = mode == "import_to_new_sequence"
    sequence_name = _first_non_empty_text(
        payload.get("output_sequence_name"),
        payload.get("sequence_name"),
        payload.get("source_sequence_name"),
        job_payload.get("output_sequence_name"),
        job_payload.get("sequence_name"),
        job_payload.get("source_sequence_name"),
    )
    requested_files = _load_requested_files(job_payload, payload)
    create_sequence = bool(
        payload.get("create_sequence_if_missing", job_payload.get("create_sequence_if_missing", True))
    )
    fail_if_sequence_exists = bool(
        payload.get(
            "fail_if_sequence_exists",
            job_payload.get("fail_if_sequence_exists", to_new_sequence),
        )
    )
    still_duration = float(payload.get("still_duration_seconds") or job_payload.get("still_duration_seconds") or _DEFAULT_STILL_SECONDS)
    write_project = bool(payload.get("write_project", True))
    template_project_raw = payload.get("template_project_path") or job_payload.get("template_project_path")
    template_project_path = Path(str(template_project_raw)) if template_project_raw else None
    reports_dir = Path(str(payload.get("reports_dir") or (project_path.parent / "media_import_reports")))
    reports_dir.mkdir(parents=True, exist_ok=True)
    default_output = str(project_path) if to_new_sequence else str(project_path.parent / f"{project_path.stem}_import.prproj")
    output_project_path = Path(str(payload.get("output_project_path") or default_output))

    progress = _build_progress_reporter(reports_dir / "sequence_media_import_progress.log")
    if not sequence_name:
        if to_new_sequence:
            raise ValueError("import_to_new_sequence requires 'output_sequence_name'.")
        sequence_name = _default_sequence_name(project_path)
        progress(f"sequence_name was empty; using '{sequence_name}'.")
    progress(f"Media-import started. Config: {config_path}")
    progress(f"Project: {project_path}")
    if root_directory is not None:
        progress(f"Root directory: {root_directory}")
    if search_roots:
        progress("Search roots: " + "; ".join(str(path) for path in search_roots))
    progress(f"Sequence: {sequence_name}")

    resolved_items = resolve_import_files(
        root_directory,
        requested_files,
        search_roots=search_roots,
    )
    for item_path in resolved_items:
        progress(f"Resolved {item_path.name} -> {item_path}")

    exported_project: Path | None = None
    warnings: list[str] = []
    imported: list[MediaImportItem] = []
    if write_project:
        exported_project, imported, export_warnings = export_media_import_premiere_project(
            source_project_path=project_path,
            output_project_path=output_project_path,
            sequence_name=sequence_name,
            source_paths=resolved_items,
            create_sequence_if_missing=create_sequence,
            fail_if_sequence_exists=fail_if_sequence_exists,
            still_duration_seconds=still_duration,
            duration_resolver=probe_media_duration_seconds,
            template_project_path=template_project_path,
        )
        warnings.extend(export_warnings)
        progress(f"Wrote project: {exported_project}")
    else:
        progress("Project write skipped (write_project=false).")

    report_payload = {
        "mode": mode,
        "source_project_path": str(project_path),
        "output_project_path": str(exported_project) if exported_project is not None else None,
        "wrote_in_place": bool(
            exported_project is not None and exported_project.resolve() == project_path.resolve()
        ),
        "import_path": str(import_path) if import_path is not None else None,
        "root_directory": str(root_directory) if root_directory is not None else None,
        "root_search_paths": [str(path) for path in search_roots],
        "sequence_name": sequence_name,
        "output_sequence_name": sequence_name,
        "create_sequence_if_missing": create_sequence,
        "fail_if_sequence_exists": fail_if_sequence_exists,
        "requested_files": [_request_to_report(item) for item in requested_files],
        "imported": [
            {
                "file": item.requested_name,
                "path": str(item.source_path),
                "kind": item.kind,
                "duration_seconds": item.duration_seconds,
                "reused_existing_media": item.reused_existing_media,
            }
            for item in imported
        ],
        "missing_files": [],
        "warnings": warnings,
    }
    json_path = reports_dir / f"{project_path.stem}_media_import.json"
    txt_path = reports_dir / f"{project_path.stem}_media_import.txt"
    json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(build_media_import_report(report_payload), encoding="utf-8")
    progress(f"Media-import completed. Report: {json_path}")
    return json_path, txt_path, exported_project


class ImportFileLookupError(ValueError):
    """Exact filename lookup failed: missing file or more than one match."""


def resolve_import_files(
    root_directory: Path | None,
    requests: list[str | ImportFileRequest | dict[str, object]],
    *,
    search_roots: list[Path] | None = None,
) -> list[Path]:
    normalized = [_normalize_file_request(item, index) for index, item in enumerate(requests, start=1)]
    normalized = sorted(
        normalized,
        key=lambda item: (item.order if item.order is not None else 10**9, item.file_name.casefold()),
    )
    roots = _combined_search_roots(root_directory, search_roots)
    need_index = any(item.source_path is None and item.relative_path is None for item in normalized)
    if need_index and not roots:
        raise ValueError(
            "Media-import config must contain 'root_directory' or 'root_search_paths' "
            "when items have no source_path."
        )
    index: dict[str, list[Path]] = {}
    if need_index:
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                index.setdefault(path.name, []).append(path)

    resolved: list[Path] = []
    errors: list[str] = []
    source_path_errors = 0
    for request in normalized:
        if request.source_path is not None:
            path, error = _resolve_declared_source_path(request)
            if error:
                source_path_errors += 1
                errors.append(error)
            else:
                resolved.append(path)
            continue
        if request.relative_path:
            if not roots:
                errors.append(f"{request.file_name}: relative_path requires root_directory or root_search_paths.")
                continue
            path, error = _resolve_relative_file(roots, request)
            if error:
                errors.append(error)
            else:
                resolved.append(path)
            continue
        file_name = request.file_name
        if file_name != Path(file_name).name or Path(file_name).suffix == "":
            errors.append(f"{file_name}: need a full filename with extension, not a path or prefix.")
            continue
        matches = _unique_existing_paths(index.get(file_name) or [])
        if len(matches) == 1:
            resolved.append(matches[0])
            continue
        if not matches:
            case_hits = [
                path
                for key, paths in index.items()
                if key.casefold() == file_name.casefold()
                for path in _unique_existing_paths(paths)
            ]
            if case_hits:
                listed = "\n    ".join(str(path) for path in case_hits)
                errors.append(
                    f"{file_name}: no exact filename match (case-sensitive). "
                    f"Same name with different case:\n    {listed}"
                )
            else:
                errors.append(f"{file_name}: not found under {_format_search_roots(roots)}")
            continue
        listed = "\n    ".join(str(path) for path in matches)
        errors.append(
            f"{file_name}: {len(matches)} files have this exact name; "
            f"set relative_path to choose one:\n    {listed}"
        )
    if errors:
        if source_path_errors == len(errors):
            header = "Media import stopped. These source_path files were not found."
        else:
            header = (
                "Media import stopped. Lookup uses the full filename with extension only "
                "(no prefix/substring match). For duplicate names use "
                '{"file": "...", "relative_path": "..."}.'
            )
        raise ImportFileLookupError(header + "\n- " + "\n- ".join(errors))
    return resolved


def _item_source_path_text(item: dict) -> str:
    return str(item.get("source_path") or item.get("source_path_hint") or "").strip()


def _normalize_file_request(item: object, index: int) -> ImportFileRequest:
    if isinstance(item, ImportFileRequest):
        return item
    if isinstance(item, str):
        name = item.strip()
        if not name:
            raise ValueError(f"Import file #{index} is empty.")
        return ImportFileRequest(file_name=name, order=index)
    if isinstance(item, dict):
        order = _optional_order(item.get("order"), index)
        source_text = _item_source_path_text(item)
        if source_text:
            source_path = Path(source_text).expanduser()
            file_name = str(item.get("file") or item.get("filename") or item.get("source_name") or "").strip()
            return ImportFileRequest(
                file_name=file_name or source_path.name,
                source_path=source_path,
                order=order,
            )
        file_name = str(item.get("file") or item.get("filename") or item.get("source_name") or "").strip()
        relative_path = str(item.get("relative_path") or "").strip() or None
        if not file_name:
            raise ValueError(f"Import file #{index} is missing 'file', 'source_name', or 'source_path'.")
        return ImportFileRequest(file_name=file_name, relative_path=relative_path, order=order)
    raise ValueError(
        f"Import file #{index} must be a filename string or an object with 'file' or 'source_path'."
    )


def _resolve_declared_source_path(request: ImportFileRequest) -> tuple[Path, str | None]:
    if request.source_path is None:
        return Path(), f"{request.file_name}: source_path is empty."
    path = request.source_path.expanduser()
    if path.is_file():
        return path.resolve(), None
    for candidate in _source_path_candidates(path):
        if candidate.is_file():
            return candidate.resolve(), None
    return Path(), f"{request.file_name}: source_path does not exist: {path}"


def _source_path_candidates(path: Path) -> list[Path]:
    names = _alternate_source_filenames(path.name)
    candidates: list[Path] = []
    if path.parent.is_dir():
        candidates.extend(path.parent / name for name in names if name != path.name)
        return candidates
    search_root = _nearest_existing_ancestor(path.parent)
    if search_root is None:
        return candidates
    for name in [path.name, *names]:
        matches = _unique_existing_paths(list(search_root.rglob(name)))
        if len(matches) == 1:
            candidates.append(matches[0])
    return candidates


def _alternate_source_filenames(file_name: str) -> list[str]:
    names: list[str] = []
    if "__" in file_name:
        names.append(file_name.replace("__", "_"))
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    if "_" in stem and "__" not in file_name:
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            names.append(f"{parts[0]}__{parts[1]}{suffix}")
    unique: list[str] = []
    for name in names:
        if name != file_name and name not in unique:
            unique.append(name)
    return unique


def _nearest_existing_ancestor(path: Path) -> Path | None:
    current = path
    for _ in range(4):
        if current.exists() and current.is_dir():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


def _optional_order(value: object, fallback: int) -> int:
    if value is None or str(value).strip() == "":
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Import item order must be an integer, got {value!r}.") from exc


def _request_to_report(item: ImportFileRequest) -> str | dict[str, object]:
    if item.source_path is not None:
        payload: dict[str, object] = {"order": item.order, "source_path": str(item.source_path)}
        return payload
    if item.relative_path:
        return {"file": item.file_name, "relative_path": item.relative_path}
    return item.file_name


def _resolve_relative_file(
    roots: list[Path],
    request: ImportFileRequest,
) -> tuple[Path, str | None]:
    relative = Path(str(request.relative_path).replace("\\", "/"))
    if relative.is_absolute():
        return Path(), f"{request.file_name}: relative_path must be relative to root_directory, not absolute."
    if relative.name != request.file_name:
        return Path(), (
            f"{request.file_name}: relative_path basename must be exactly '{request.file_name}', "
            f"got '{relative.name}'."
        )
    matches: list[Path] = []
    missing: list[str] = []
    for root_directory in roots:
        candidate = root_directory / relative
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root_directory.resolve())
        except ValueError:
            missing.append(f"{request.file_name}: relative_path escapes {root_directory}: {relative}")
            continue
        if resolved.is_file():
            matches.append(resolved)
        else:
            missing.append(str(resolved))
    unique = _unique_existing_paths(matches)
    if len(unique) == 1:
        return unique[0], None
    if not unique:
        listed = "\n    ".join(missing) if missing else str(relative)
        return Path(), f"{request.file_name}: relative_path does not exist:\n    {listed}"
    listed = "\n    ".join(str(path) for path in unique)
    return Path(), (
        f"{request.file_name}: {len(unique)} files match relative_path; "
        f"narrow root_search_paths or use source_path:\n    {listed}"
    )


def _default_sequence_name(project_path: Path) -> str:
    names = list_named_project_sequence_names(load_premiere_project_root(project_path))
    for name in names:
        if name.casefold() not in {"lib", "library"}:
            return name
    if names:
        return names[0]
    return project_path.stem


def _unique_existing_paths(paths: list[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        if not path.is_file():
            continue
        unique[str(path.resolve())] = path
    return sorted(unique.values(), key=lambda path: str(path).casefold())


def probe_media_duration_seconds(path: Path) -> float | None:
    if is_supported_image_media_path(path):
        return None
    if not is_supported_visual_media_path(path):
        return None
    try:
        ffmpeg = resolve_ffmpeg_executable()
    except RuntimeError:
        return None
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    match = _DURATION_PATTERN.search(completed.stderr or "")
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def build_media_import_report(payload: dict[str, object]) -> str:
    lines = [
        "Sequence Media Import",
        "=" * 72,
        f"Source project: {payload.get('source_project_path')}",
        f"Root directory: {payload.get('root_directory')}",
        f"Sequence: {payload.get('sequence_name')}",
    ]
    search_roots = payload.get("root_search_paths") or []
    if search_roots:
        lines.append("Search roots: " + "; ".join(str(path) for path in search_roots))
    if payload.get("output_project_path"):
        lines.append(f"Output project: {payload['output_project_path']}")
    if payload.get("wrote_in_place"):
        lines.append("Wrote in place: true")
    lines.extend(["", "Imported files:", "-" * 72])
    imported = payload.get("imported") or []
    if not imported:
        lines.append("- none")
    for item in imported:
        if not isinstance(item, dict):
            continue
        reused = "reuse" if item.get("reused_existing_media") else "new"
        lines.append(
            f"- {item.get('file')}  [{item.get('kind')}/{reused}]  "
            f"{float(item.get('duration_seconds', 0)):.3f}s"
        )
        lines.append(f"    {item.get('path')}")
    missing = [str(name) for name in (payload.get("missing_files") or [])]
    if missing:
        lines.extend(["", "Missing files:", "-" * 72])
        lines.extend(f"- {name}" for name in missing)
    warnings = [str(item) for item in (payload.get("warnings") or [])]
    if warnings:
        lines.extend(["", "Warnings:", "-" * 72])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "How to use in Premiere:",
            "-" * 72,
            "1. Open the output .prproj. import_media writes a copy; import_to_new_sequence writes the same project.",
            "2. Open the named sequence; listed files are appended in JSON order.",
            "3. Files already present in the project reuse the existing media object.",
            "4. New files come from exact filename lookup, relative_path, or absolute source_path.",
            "5. Existing sequences stay unchanged when the import created a new sequence.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_search_roots(
    job_payload: dict[str, object],
    payload: dict[str, object],
) -> tuple[Path | None, list[Path]]:
    root_raw = job_payload.get("root_directory") or payload.get("root_directory")
    root_directory = (
        _resolve_existing_directory(root_raw, label="root_directory")
        if str(root_raw or "").strip()
        else None
    )
    raw_paths = payload.get("root_search_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raw_paths = job_payload.get("root_search_paths")
    if isinstance(raw_paths, str) and raw_paths.strip():
        raw_paths = [raw_paths]
    search_roots: list[Path] = []
    if isinstance(raw_paths, list):
        for index, item in enumerate(raw_paths, start=1):
            text = str(item or "").strip()
            if not text:
                continue
            search_roots.append(_resolve_existing_directory(text, label=f"root_search_paths[{index}]"))
    return root_directory, search_roots


def _combined_search_roots(
    root_directory: Path | None,
    search_roots: list[Path] | None,
) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for path in [root_directory, *(search_roots or [])]:
        if path is None:
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def _format_search_roots(roots: list[Path]) -> str:
    if not roots:
        return "root_directory/root_search_paths"
    return "; ".join(str(path) for path in roots)


def _load_import_job(payload: dict[str, object], config_path: Path) -> tuple[dict[str, object], Path | None]:
    import_value = str(payload.get("import_path") or payload.get("keep_ranges_path") or "").strip()
    if not import_value:
        return payload, None
    import_path = Path(import_value)
    if not import_path.is_file() and not import_path.is_absolute():
        import_path = (config_path.parent / import_path).resolve()
    if not import_path.is_file():
        raise FileNotFoundError(f"Import JSON does not exist: {import_path}")
    job_payload = json.loads(import_path.read_text(encoding="utf-8"))
    if not isinstance(job_payload, dict):
        raise ValueError(f"Import JSON must be an object: {import_path}")
    return job_payload, import_path


def _first_non_empty_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _load_requested_files(
    job_payload: dict[str, object],
    payload: dict[str, object],
) -> list[ImportFileRequest]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raw_items = job_payload.get("items")
    if isinstance(raw_items, list) and raw_items:
        requests = [_normalize_file_request(item, index) for index, item in enumerate(raw_items, start=1)]
        return requests
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raw_files = job_payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("Media-import config must contain a non-empty 'items' or 'files' list.")
    requests = [_normalize_file_request(item, index) for index, item in enumerate(raw_files, start=1)]
    if not requests:
        raise ValueError("Media-import config must contain a non-empty 'items' or 'files' list.")
    return requests


def _resolve_existing_path(value: object, *, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Media-import config must contain '{label}'.")
    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return path


def _resolve_existing_directory(value: object, *, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Media-import config must contain '{label}'.")
    path = Path(text).expanduser()
    if not path.is_dir():
        raise FileNotFoundError(f"{label} is not a directory: {path}")
    return path


def _build_progress_reporter(log_path: Path) -> Callable[[str], None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def report(message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    return report
