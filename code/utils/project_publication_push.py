from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from utils.project_publication import write_publication_bundle


DEFAULT_PUBLICATION_REMOTE = "https://github.com/sergey-frd/Memory-to-Video_Agent.git"


@dataclass
class PublicationPushResult:
    repo_dir: str
    remote_url: str | None
    branch: str | None
    managed_files: list[str]
    removed_stale_files: list[str]
    staged_files: list[str]
    committed: bool
    pushed: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=check,
    )


def _repo_is_git(repo_dir: Path) -> bool:
    return (repo_dir / ".git").exists()


def _normalize_remote(url: str) -> str:
    text = url.strip()
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text.split("git@github.com:", 1)[1]
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/").lower()


def get_remote_url(repo_dir: Path, remote_name: str = "origin") -> str | None:
    result = _run_git(repo_dir, "remote", "get-url", remote_name, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def verify_expected_remote(repo_dir: Path, expected_remote_url: str, remote_name: str = "origin") -> str:
    remote_url = get_remote_url(repo_dir, remote_name=remote_name)
    if remote_url is None:
        raise ValueError(f"Git remote '{remote_name}' was not found in {repo_dir}.")
    if _normalize_remote(remote_url) != _normalize_remote(expected_remote_url):
        raise ValueError(
            f"Remote '{remote_name}' points to '{remote_url}', expected '{expected_remote_url}'."
        )
    return remote_url


def _load_previous_managed_files(repo_dir: Path) -> list[str]:
    manifest_path = repo_dir / "data" / "publication_manifest.json"
    if not manifest_path.exists():
        return []
    import json

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    managed_files = payload.get("managed_files", [])
    return [str(item) for item in managed_files if str(item).strip()]


def _path_within_repo(repo_dir: Path, relpath: str) -> Path:
    candidate = (repo_dir / relpath).resolve(strict=False)
    candidate.relative_to(repo_dir.resolve(strict=False))
    return candidate


def remove_stale_managed_files(repo_dir: Path, previous_files: list[str], current_files: list[str]) -> list[str]:
    stale_files = sorted(set(previous_files) - set(current_files))
    removed: list[str] = []
    for relpath in stale_files:
        target = _path_within_repo(repo_dir, relpath)
        if not target.exists() or not target.is_file():
            continue
        _unlink_with_retry(target)
        removed.append(relpath)
    return removed


def _unlink_with_retry(path: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            last_error = exc
            try:
                os.chmod(path, 0o666)
            except OSError:
                pass
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def stage_publication_files(repo_dir: Path, relpaths: list[str]) -> list[str]:
    staged = sorted({relpath for relpath in relpaths if relpath})
    if not staged:
        return []
    _run_git(repo_dir, "add", "--all", "--", *staged)
    return staged


def _has_staged_changes(repo_dir: Path) -> bool:
    result = _run_git(repo_dir, "diff", "--cached", "--quiet", check=False)
    return result.returncode == 1


def _current_branch(repo_dir: Path) -> str | None:
    result = _run_git(repo_dir, "branch", "--show-current", check=False)
    branch = result.stdout.strip()
    return branch or None


def commit_publication_changes(repo_dir: Path, message: str) -> bool:
    if not _has_staged_changes(repo_dir):
        return False
    _run_git(repo_dir, "commit", "-m", message)
    return True


def push_publication_changes(repo_dir: Path, remote_name: str = "origin", branch: str | None = None) -> bool:
    branch_name = branch or _current_branch(repo_dir)
    if not branch_name:
        raise ValueError("Could not determine the current git branch for push.")
    _run_git(repo_dir, "push", remote_name, branch_name)
    return True


def sync_publication_repo(
    *,
    source_root: Path,
    repo_dir: Path,
    registry_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    previous_files = _load_previous_managed_files(repo_dir)
    publication_result = write_publication_bundle(source_root=source_root, target_dir=repo_dir, registry_path=registry_path)
    current_files = list(publication_result.written_files)
    removed_files = remove_stale_managed_files(repo_dir, previous_files, current_files)
    return current_files, removed_files


def prepare_publication_push(
    *,
    source_root: Path,
    repo_dir: Path,
    registry_path: Path | None = None,
    expected_remote_url: str = DEFAULT_PUBLICATION_REMOTE,
    remote_name: str = "origin",
    stage: bool = False,
    commit_message: str | None = None,
    push: bool = False,
) -> PublicationPushResult:
    if not _repo_is_git(repo_dir):
        raise ValueError(f"Target repo directory is not a git repository: {repo_dir}")

    remote_url = verify_expected_remote(repo_dir, expected_remote_url, remote_name=remote_name)
    managed_files, removed_stale_files = sync_publication_repo(
        source_root=source_root,
        repo_dir=repo_dir,
        registry_path=registry_path,
    )

    should_stage = stage or bool(commit_message) or push
    staged_files: list[str] = []
    if should_stage:
        staged_files = stage_publication_files(repo_dir, managed_files + removed_stale_files)

    committed = False
    if commit_message:
        committed = commit_publication_changes(repo_dir, commit_message)

    if push and not commit_message and _has_staged_changes(repo_dir):
        raise ValueError("Refusing to push with newly staged publication changes unless --commit-message is provided.")

    pushed = False
    if push:
        pushed = push_publication_changes(repo_dir, remote_name=remote_name)

    return PublicationPushResult(
        repo_dir=str(repo_dir.resolve()),
        remote_url=remote_url,
        branch=_current_branch(repo_dir),
        managed_files=managed_files,
        removed_stale_files=removed_stale_files,
        staged_files=staged_files,
        committed=committed,
        pushed=pushed,
    )
