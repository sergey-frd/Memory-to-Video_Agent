from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2, copytree

from config import Settings
from utils.project_delivery import remove_path


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    category: str
    reason: str
    age_days: float

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "category": self.category,
            "reason": self.reason,
            "age_days": round(self.age_days, 3),
        }


@dataclass(frozen=True)
class CleanupSummary:
    project_root: Path
    candidates: list[CleanupCandidate]
    deleted_paths: list[Path]
    archived_paths: list[Path]
    dry_run: bool
    older_than_days: float | None
    archive_dir: Path | None

    def to_dict(self) -> dict[str, object]:
        return {
            "project_root": str(self.project_root),
            "dry_run": self.dry_run,
            "older_than_days": self.older_than_days,
            "archive_dir": str(self.archive_dir) if self.archive_dir else None,
            "candidate_count": len(self.candidates),
            "deleted_count": len(self.deleted_paths),
            "archived_count": len(self.archived_paths),
            "candidates": [item.to_dict() for item in self.candidates],
            "deleted_paths": [str(path) for path in self.deleted_paths],
            "archived_paths": [str(path) for path in self.archived_paths],
        }


def discover_cleanup_candidates(
    *,
    settings: Settings | None = None,
    extra_reports_dirs: list[Path] | None = None,
    older_than_days: float | None = None,
    include_output_build_dirs: bool = False,
    include_output_files: bool = False,
    include_test_runtime_items: bool = False,
    now: datetime | None = None,
) -> list[CleanupCandidate]:
    settings = settings or Settings()
    current_time = now or datetime.now(timezone.utc)
    seen: dict[Path, CleanupCandidate] = {}

    def consider(path: Path, *, category: str, reason: str) -> None:
        if not path.exists():
            return
        age_days = _age_in_days(path, current_time)
        if older_than_days is not None and age_days < older_than_days:
            return
        resolved = path.resolve(strict=False)
        seen[resolved] = CleanupCandidate(
            path=path,
            category=category,
            reason=reason,
            age_days=age_days,
        )

    consider(settings.project_root / ".pytest_cache", category="cache", reason="pytest cache directory")
    consider(settings.project_root / ".pytest-temp", category="cache", reason="pytest temporary directory")

    if include_test_runtime_items:
        test_runtime_dir = settings.project_root / "test_runtime"
        if test_runtime_dir.exists():
            for child in test_runtime_dir.iterdir():
                consider(child, category="test-runtime-item", reason="test runtime artifact")

    for pycache_dir in settings.project_root.rglob("__pycache__"):
        if ".venv" in pycache_dir.parts or "site-packages" in pycache_dir.parts:
            continue
        consider(pycache_dir, category="cache", reason="Python bytecode cache")

    if settings.output_dir.exists():
        for staging_dir in settings.output_dir.rglob("*"):
            if staging_dir.is_dir() and staging_dir.name.endswith("_staging"):
                consider(staging_dir, category="staging", reason="staging reports directory")

        if include_output_build_dirs:
            for child in settings.output_dir.iterdir():
                if child.name == "cleanup_reports":
                    consider(child, category="cleanup-report", reason="cleanup report directory")
                    continue
                if _is_legacy_output_build_dir(child):
                    consider(child, category="legacy-output-build", reason="legacy output build directory")

        if include_output_files:
            for child in settings.output_dir.iterdir():
                if _is_generated_output_file(child):
                    consider(child, category="legacy-output-file", reason="generated output artifact in workspace output")

    for reports_dir in extra_reports_dirs or []:
        consider(reports_dir / "temp_projects", category="temp-projects", reason="intermediate optimized sequence projects")
        if reports_dir.parent.exists():
            for sibling in reports_dir.parent.iterdir():
                if sibling.is_dir() and sibling.name.endswith("_staging"):
                    consider(sibling, category="staging", reason="staging reports directory beside reports")

    return _prune_nested_candidates(seen.values())


def execute_cleanup(
    candidates: list[CleanupCandidate],
    *,
    project_root: Path,
    archive_dir: Path | None = None,
    dry_run: bool = True,
    older_than_days: float | None = None,
) -> CleanupSummary:
    archived_paths: list[Path] = []
    deleted_paths: list[Path] = []

    ordered_candidates = sorted(candidates, key=lambda item: (len(item.path.parts), str(item.path)))
    archive_manifest_items: list[dict[str, object]] = []

    if archive_dir is not None and not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for candidate in ordered_candidates:
        if not candidate.path.exists():
            continue
        archived_target: Path | None = None
        if archive_dir is not None:
            archived_target = archive_dir / _archive_relative_path(candidate.path)
            archive_manifest_items.append(
                {
                    **candidate.to_dict(),
                    "archived_to": str(archived_target),
                }
            )
            if not dry_run:
                _archive_path(candidate.path, archived_target)
                archived_paths.append(archived_target)

        if not dry_run:
            remove_path(candidate.path)
            deleted_paths.append(candidate.path)

    if archive_dir is not None and archive_manifest_items:
        manifest_path = archive_dir / "cleanup_manifest.json"
        manifest_payload = {
            "project_root": str(project_root),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": archive_manifest_items,
        }
        if dry_run:
            archived_paths.append(manifest_path)
        else:
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            archived_paths.append(manifest_path)

    return CleanupSummary(
        project_root=project_root,
        candidates=ordered_candidates,
        deleted_paths=deleted_paths,
        archived_paths=archived_paths,
        dry_run=dry_run,
        older_than_days=older_than_days,
        archive_dir=archive_dir,
    )


def write_cleanup_report(
    summary: CleanupSummary,
    *,
    output_json: Path,
    output_txt: Path,
) -> tuple[Path, Path]:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    output_txt.write_text(_format_cleanup_summary(summary), encoding="utf-8")
    return output_json, output_txt


def derive_cleanup_report_paths(settings: Settings, *, timestamp: str) -> tuple[Path, Path]:
    report_dir = settings.output_dir / "cleanup_reports"
    return (
        report_dir / f"cleanup_{timestamp}.json",
        report_dir / f"cleanup_{timestamp}.txt",
    )


def _prune_nested_candidates(candidates: object) -> list[CleanupCandidate]:
    ordered = sorted(
        list(candidates),
        key=lambda item: (len(item.path.resolve(strict=False).parts), str(item.path.resolve(strict=False))),
    )
    kept: list[CleanupCandidate] = []
    kept_resolved: list[Path] = []
    for candidate in ordered:
        resolved = candidate.path.resolve(strict=False)
        if any(_is_within_directory(resolved, parent) for parent in kept_resolved):
            continue
        kept.append(candidate)
        kept_resolved.append(resolved)
    return kept


def _is_within_directory(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _age_in_days(path: Path, now: datetime) -> float:
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now - modified_at).total_seconds() / 86400.0)


def _is_legacy_output_build_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return (
        path.name.endswith("_prproj_safe_build")
        or "_sequence_batch" in path.name
        or path.name.endswith("_sequence_single_runs")
        or path.name.endswith("_prproj_transitions_debug")
        or "rerun" in path.name
        or (path / "temp_projects").exists()
        or (path / "batch_summary.json").exists()
        or (path / "batch_summary.txt").exists()
    )


def _is_generated_output_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if path.suffix.lower() not in {".json", ".txt", ".xml", ".prproj"}:
        return False
    name = path.name.lower()
    return any(
        hint in name
        for hint in (
            "optimized_sequence",
            "optimized_project_sequence",
            "transition_recommendations",
            "batch_summary",
            "_optimized.prproj",
        )
    )


def _archive_relative_path(path: Path) -> Path:
    anchor = path.anchor.replace(":", "").replace("\\", "").replace("/", "")
    parts = [anchor] if anchor else []
    parts.extend(part for part in path.parts if part != path.anchor)
    return Path(*parts)


def _archive_path(source_path: Path, target_path: Path) -> None:
    if target_path.exists():
        remove_path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.is_dir():
        copytree(source_path, target_path)
    else:
        copy2(source_path, target_path)


def _format_cleanup_summary(summary: CleanupSummary) -> str:
    lines = [
        "ARTIFACT CLEANUP REPORT",
        "",
        f"Project root: {summary.project_root}",
        f"Dry run: {summary.dry_run}",
        f"Older than days: {summary.older_than_days if summary.older_than_days is not None else '<disabled>'}",
        f"Archive dir: {summary.archive_dir or '<disabled>'}",
        f"Candidates: {len(summary.candidates)}",
        f"Deleted: {len(summary.deleted_paths)}",
        f"Archived items: {len(summary.archived_paths)}",
        "",
    ]
    if not summary.candidates:
        lines.append("No cleanup candidates were found.")
        lines.append("")
        return "\n".join(lines)

    lines.append("Candidates")
    lines.append("")
    for item in summary.candidates:
        lines.append(
            f"- [{item.category}] {item.path} | age {item.age_days:.2f} days | {item.reason}"
        )
    lines.append("")
    return "\n".join(lines)
