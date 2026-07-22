#!/usr/bin/env python3
"""Primitive environment snapshot/compare for laptop watercolor batch setup."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = PROJECT_ROOT / "env_baseline_chatgpt_watercolor.json"

DEFAULT_REQUIRED_FILES = (
    "main_chatgpt_portrait_batch.py",
    "run_chatgpt_portrait_batch_existing.bat",
    "run_chatgpt_watercolor_on_paper_existing.bat",
    "chatgpt_watercolor_on_paper_config.json",
    "config_Ziggi.json",
)

DEFAULT_REQUIRED_DIRS = (
    "input",
    "output",
    "models",
)

DEFAULT_OPTIONAL_DIRS = (
    "output/chatgpt_watercolor_on_paper",
)


@dataclass(frozen=True)
class RequirementSpec:
    name: str
    operator: str | None = None
    version: str | None = None


def _parse_requirement_line(line: str) -> RequirementSpec | None:
    clean = line.strip().lstrip("\ufeff")
    if not clean or clean.startswith("#"):
        return None
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(==|>=|<=|>|<)?\s*([A-Za-z0-9_.-]+)?$", clean)
    if not match:
        return RequirementSpec(name=clean)
    return RequirementSpec(
        name=match.group(1),
        operator=match.group(2),
        version=match.group(3),
    )


def _split_version(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", version)
    return tuple(int(x) for x in numbers) if numbers else (0,)


def _compare_versions(left: str, right: str) -> int:
    l = _split_version(left)
    r = _split_version(right)
    size = max(len(l), len(r))
    l_pad = l + (0,) * (size - len(l))
    r_pad = r + (0,) * (size - len(r))
    if l_pad < r_pad:
        return -1
    if l_pad > r_pad:
        return 1
    return 0


def _requirement_ok(installed_version: str, spec: RequirementSpec) -> bool:
    if spec.operator is None or spec.version is None:
        return True
    cmp_value = _compare_versions(installed_version, spec.version)
    if spec.operator == "==":
        return cmp_value == 0
    if spec.operator == ">=":
        return cmp_value >= 0
    if spec.operator == "<=":
        return cmp_value <= 0
    if spec.operator == ">":
        return cmp_value > 0
    if spec.operator == "<":
        return cmp_value < 0
    return True


def _collect_requirements(requirements_path: Path) -> list[RequirementSpec]:
    specs: list[RequirementSpec] = []
    if not requirements_path.exists():
        return specs
    for raw in requirements_path.read_text(encoding="utf-8").splitlines():
        spec = _parse_requirement_line(raw)
        if spec is not None:
            specs.append(spec)
    return specs


def _collect_installed_versions(specs: list[RequirementSpec]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for spec in specs:
        try:
            result[spec.name] = metadata.version(spec.name)
        except metadata.PackageNotFoundError:
            result[spec.name] = None
    return result


def _path_exists_list(project_root: Path, rel_paths: tuple[str, ...]) -> dict[str, bool]:
    return {rel: (project_root / rel).exists() for rel in rel_paths}


def build_snapshot(project_root: Path) -> dict[str, Any]:
    requirements_path = project_root / "requirements.txt"
    requirement_specs = _collect_requirements(requirements_path)
    installed = _collect_installed_versions(requirement_specs)
    return {
        "snapshot_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_root": str(project_root),
        "python": {
            "version": platform.python_version(),
            "major_minor": ".".join(platform.python_version_tuple()[:2]),
            "implementation": platform.python_implementation(),
            "executable": str(Path(sys.executable).resolve()),
        },
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "requirements_file": str(requirements_path),
        "requirements": [
            {
                "name": spec.name,
                "operator": spec.operator,
                "required_version": spec.version,
                "installed_version": installed.get(spec.name),
            }
            for spec in requirement_specs
        ],
        "required_files": _path_exists_list(project_root, DEFAULT_REQUIRED_FILES),
        "required_dirs": _path_exists_list(project_root, DEFAULT_REQUIRED_DIRS),
        "optional_dirs": _path_exists_list(project_root, DEFAULT_OPTIONAL_DIRS),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_python(baseline: dict[str, Any], current: dict[str, Any], problems: list[str]) -> None:
    base_mm = baseline.get("python", {}).get("major_minor")
    cur_mm = current.get("python", {}).get("major_minor")
    if base_mm != cur_mm:
        problems.append(f"Python major.minor mismatch: baseline={base_mm}, current={cur_mm}")


def _check_requirements(
    baseline: dict[str, Any],
    current: dict[str, Any],
    strict_versions: bool,
    problems: list[str],
) -> None:
    _ = baseline
    current_requirements = current.get("requirements", [])
    for req in current_requirements:
        name = req.get("name")
        installed = req.get("installed_version")
        op = req.get("operator")
        required_version = req.get("required_version")
        if installed is None:
            problems.append(f"Package missing: {name}")
            continue
        if strict_versions and required_version:
            if not _requirement_ok(installed, RequirementSpec(name=name, operator=op, version=required_version)):
                problems.append(
                    f"Package version mismatch: {name} installed={installed}, required={op}{required_version}"
                )


def _check_paths(current: dict[str, Any], problems: list[str]) -> None:
    for rel_path, exists in current.get("required_files", {}).items():
        if not exists:
            problems.append(f"Required file missing: {rel_path}")
    for rel_path, exists in current.get("required_dirs", {}).items():
        if not exists:
            problems.append(f"Required directory missing: {rel_path}")


def _print_optional_dir_notes(current: dict[str, Any]) -> None:
    for rel_path, exists in current.get("optional_dirs", {}).items():
        if not exists:
            print(f"[WARN] Optional directory is missing (will be auto-created on first run): {rel_path}")


def save_snapshot(snapshot: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_snapshot(args: argparse.Namespace) -> int:
    snapshot = build_snapshot(args.project_root)
    save_snapshot(snapshot, args.output)
    print(f"[OK] Snapshot saved: {args.output}")
    print(f"[INFO] Python: {snapshot['python']['version']} ({snapshot['python']['implementation']})")
    print(f"[INFO] Requirements checked: {len(snapshot['requirements'])}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    if not args.baseline.exists():
        print(f"[ERROR] Baseline file not found: {args.baseline}")
        return 2

    baseline = _read_json(args.baseline)
    current = build_snapshot(args.project_root)
    problems: list[str] = []

    _check_python(baseline, current, problems)
    _check_requirements(baseline, current, args.strict_versions, problems)
    _check_paths(current, problems)
    _print_optional_dir_notes(current)

    if problems:
        print("[FAIL] Environment check failed:")
        for item in problems:
            print(f"  - {item}")
        if args.report:
            args.report.write_text(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[INFO] Report saved: {args.report}")
        return 1

    print("[OK] Environment looks compatible for watercolor batch.")
    if args.report:
        args.report.write_text(json.dumps({"ok": True, "problems": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[INFO] Report saved: {args.report}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Primitive environment check for portable watercolor portrait setup."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project root to validate. Defaults to this repository root.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="Create baseline JSON from current machine.")
    snapshot_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="Where to save baseline JSON.",
    )
    snapshot_parser.set_defaults(handler=cmd_snapshot)

    compare_parser = subparsers.add_parser("compare", help="Compare current machine against baseline JSON.")
    compare_parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="Baseline JSON created on the reference machine.",
    )
    compare_parser.add_argument(
        "--strict-versions",
        action="store_true",
        help="Also enforce requirements.txt version constraints.",
    )
    compare_parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON report path.",
    )
    compare_parser.set_defaults(handler=cmd_compare)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.project_root = args.project_root.resolve()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
