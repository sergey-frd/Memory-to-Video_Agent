import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from config import Settings
from utils.artifact_cleanup import CleanupCandidate, discover_cleanup_candidates, execute_cleanup


def _settings_for(root: Path) -> Settings:
    settings = Settings(project_root=root)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _set_age_in_days(path: Path, *, days: float, now: datetime) -> None:
    timestamp = (now - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_discover_cleanup_candidates_filters_by_age_and_reports_dirs() -> None:
    root = Path("tmp_cleanup_artifacts") / f"artifact_cleanup_{uuid4().hex}"
    settings = _settings_for(root)
    now = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)

    pytest_cache = root / ".pytest_cache"
    pytest_cache.mkdir(parents=True, exist_ok=True)
    _set_age_in_days(pytest_cache, days=12, now=now)

    staging_dir = settings.output_dir / "sample_batch_config_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    _set_age_in_days(staging_dir, days=9, now=now)

    reports_dir = root / "reports"
    temp_projects = reports_dir / "temp_projects"
    temp_projects.mkdir(parents=True, exist_ok=True)
    _set_age_in_days(temp_projects, days=2, now=now)

    cleanup_reports = settings.output_dir / "cleanup_reports"
    cleanup_reports.mkdir(parents=True, exist_ok=True)
    _set_age_in_days(cleanup_reports, days=8, now=now)

    test_runtime_dir = root / "test_runtime"
    old_runtime_item = test_runtime_dir / "manual_group_offset_check"
    old_runtime_item.mkdir(parents=True, exist_ok=True)
    _set_age_in_days(old_runtime_item, days=10, now=now)

    candidates = discover_cleanup_candidates(
        settings=settings,
        extra_reports_dirs=[reports_dir],
        older_than_days=7,
        include_output_build_dirs=True,
        include_test_runtime_items=True,
        now=now,
    )

    candidate_paths = {candidate.path for candidate in candidates}
    assert pytest_cache in candidate_paths
    assert staging_dir in candidate_paths
    assert cleanup_reports in candidate_paths
    assert old_runtime_item in candidate_paths
    assert temp_projects not in candidate_paths


def test_execute_cleanup_archives_then_deletes_candidates(monkeypatch) -> None:
    root = Path("tmp_cleanup_artifacts") / f"artifact_cleanup_exec_{uuid4().hex}"
    settings = _settings_for(root)

    staging_dir = settings.output_dir / "legacy_batch_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "batch_summary.txt").write_text("summary", encoding="utf-8")

    archive_dir = root / "cleanup_archive"
    candidates = [
        CleanupCandidate(
            path=staging_dir,
            category="staging",
            reason="staging reports directory",
            age_days=3.0,
        ),
    ]
    removed_paths: list[Path] = []

    def fake_remove_path(path: Path) -> None:
        removed_paths.append(path)

    monkeypatch.setattr("utils.artifact_cleanup.remove_path", fake_remove_path)

    summary = execute_cleanup(
        candidates,
        project_root=settings.project_root,
        archive_dir=archive_dir,
        dry_run=False,
    )

    assert removed_paths == [staging_dir]
    assert len(summary.deleted_paths) == 1
    manifest_path = archive_dir / "cleanup_manifest.json"
    assert manifest_path.exists()
    archived_names = {path.name for path in summary.archived_paths}
    assert "cleanup_manifest.json" in archived_names
    assert any(path.exists() and path.is_dir() and path.name == "legacy_batch_staging" for path in archive_dir.rglob("*"))
