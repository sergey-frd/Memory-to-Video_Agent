from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from utils.project_publication_push import (
    DEFAULT_PUBLICATION_REMOTE,
    prepare_publication_push,
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
        description="Safely refresh, stage, commit, and optionally push the publication bundle into the public Memory-to-Video_Agent repository clone."
    )
    parser.add_argument("--repo-dir", type=Path, required=True, help="Local git clone of Memory-to-Video_Agent.")
    parser.add_argument("--source-root", type=Path, default=Path("."), help="Source project root.")
    parser.add_argument("--registry", type=Path, default=None, help="Optional explicit path to project_structure_registry.json.")
    parser.add_argument(
        "--expected-remote-url",
        type=str,
        default=DEFAULT_PUBLICATION_REMOTE,
        help="Expected git remote URL for safety verification.",
    )
    parser.add_argument("--remote-name", type=str, default="origin", help="Remote name to verify and push to.")
    parser.add_argument("--stage", action="store_true", help="Stage only the managed publication files.")
    parser.add_argument("--commit-message", type=str, default=None, help="Optional commit message. Implies staging.")
    parser.add_argument("--push", action="store_true", help="Push after commit. Requires --commit-message if new staged changes exist.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
    return parser.parse_args()


def main() -> None:
    _configure_stdio()
    args = parse_args()
    result = prepare_publication_push(
        source_root=args.source_root,
        repo_dir=args.repo_dir,
        registry_path=args.registry,
        expected_remote_url=args.expected_remote_url,
        remote_name=args.remote_name,
        stage=args.stage,
        commit_message=args.commit_message,
        push=args.push,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"Publication repo prepared: {result.repo_dir}")
    print(f"Remote: {result.remote_url}")
    print(f"Branch: {result.branch or '<unknown>'}")
    print(f"Version: {result.publication_version or '<unknown>'}")
    print(f"Git tag: {result.git_tag or '<none>'}")
    print(f"Managed files: {len(result.managed_files)}")
    print(f"Removed stale files: {len(result.removed_stale_files)}")
    print(f"Staged files: {len(result.staged_files)}")
    print(f"Tagged: {result.tagged}")
    print(f"Committed: {result.committed}")
    print(f"Pushed: {result.pushed}")


if __name__ == "__main__":
    main()
