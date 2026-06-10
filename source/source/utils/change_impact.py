from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _normalize_path_text(value: str | Path, *, project_root: Path | None = None) -> str:
    text = str(value).strip()
    if not text:
        return ""
    path = Path(text)
    if project_root is not None:
        candidate = path if path.is_absolute() else project_root / path
        try:
            text = candidate.resolve(strict=False).relative_to(project_root.resolve(strict=False)).as_posix()
        except ValueError:
            text = path.as_posix()
    else:
        text = path.as_posix()
    return text.lstrip("./")


def _matches_path_pattern(path_text: str, pattern: str) -> bool:
    normalized_path = _normalize_path_text(path_text).lower()
    normalized_pattern = _normalize_path_text(pattern).lower()
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if "/" not in normalized_pattern and fnmatch.fnmatch(Path(normalized_path).name, normalized_pattern):
        return True
    return False


def _pattern_overlap(left: str, right: str) -> bool:
    left_norm = _normalize_path_text(left).lower()
    right_norm = _normalize_path_text(right).lower()
    if left_norm == right_norm:
        return True
    return _matches_path_pattern(left_norm, right_norm) or _matches_path_pattern(right_norm, left_norm)


def _matching_files(changed_files: list[str], patterns: list[str]) -> list[str]:
    matches: list[str] = []
    for changed_file in changed_files:
        if any(_matches_path_pattern(changed_file, pattern) for pattern in patterns):
            matches.append(changed_file)
    return _dedupe(matches)


def _patterns_overlap(left_patterns: list[str], right_patterns: list[str]) -> bool:
    for left in left_patterns:
        for right in right_patterns:
            if _pattern_overlap(left, right):
                return True
    return False


@dataclass
class ImpactChangeType:
    id: str
    description: str
    reason: str
    matched_files: list[str] = field(default_factory=list)
    must_touch: list[str] = field(default_factory=list)
    must_review: list[str] = field(default_factory=list)
    minimum_checks: list[str] = field(default_factory=list)
    recommended_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactSubsystem:
    id: str
    purpose: str
    reason: str
    matched_files: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    must_review_when_changed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactReport:
    project: str
    registry_path: str
    changed_files: list[str]
    selected_change_types: list[ImpactChangeType]
    inferred_change_types: list[str]
    matched_subsystems: list[ImpactSubsystem]
    files_to_touch: list[str]
    files_to_review: list[str]
    tests_to_run: list[str]
    documents_to_review: list[str]
    minimum_checks: list[str]
    core_invariants: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "registry_path": self.registry_path,
            "changed_files": list(self.changed_files),
            "selected_change_types": [item.to_dict() for item in self.selected_change_types],
            "inferred_change_types": list(self.inferred_change_types),
            "matched_subsystems": [item.to_dict() for item in self.matched_subsystems],
            "files_to_touch": list(self.files_to_touch),
            "files_to_review": list(self.files_to_review),
            "tests_to_run": list(self.tests_to_run),
            "documents_to_review": list(self.documents_to_review),
            "minimum_checks": list(self.minimum_checks),
            "core_invariants": list(self.core_invariants),
            "notes": list(self.notes),
        }


def load_change_registry(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required_keys = {"project", "canonical_docs", "core_invariants", "subsystems", "change_types"}
    missing = sorted(required_keys - set(data))
    if missing:
        raise ValueError(f"Registry is missing required key(s): {', '.join(missing)}")
    return data


def available_change_types(registry: dict[str, Any]) -> list[str]:
    return [str(item["id"]) for item in registry.get("change_types", [])]


def infer_change_type_ids(registry: dict[str, Any], changed_files: list[str]) -> list[str]:
    scored: list[tuple[int, str]] = []
    for change_type in registry.get("change_types", []):
        must_touch = [str(item) for item in change_type.get("must_touch", [])]
        must_review = [str(item) for item in change_type.get("must_review", [])]
        touch_matches = _matching_files(changed_files, must_touch)
        review_matches = _matching_files(changed_files, must_review)
        score = len(touch_matches) * 3 + len(review_matches)
        if score > 0:
            scored.append((score, str(change_type["id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [change_type_id for _score, change_type_id in scored]


def build_impact_report(
    registry_path: Path,
    *,
    change_type_ids: list[str] | None = None,
    changed_files: list[str] | None = None,
    project_root: Path | None = None,
) -> ImpactReport:
    registry = load_change_registry(registry_path)
    project_root = project_root or registry_path.resolve().parent
    normalized_changed_files = _dedupe(
        [
            _normalize_path_text(path, project_root=project_root)
            for path in (changed_files or [])
            if _normalize_path_text(path, project_root=project_root)
        ]
    )

    known_change_types = {str(item["id"]): item for item in registry.get("change_types", [])}
    explicit_change_type_ids = _dedupe([item for item in (change_type_ids or []) if item])
    unknown_change_types = sorted(set(explicit_change_type_ids) - set(known_change_types))
    if unknown_change_types:
        raise ValueError(
            "Unknown change type(s): "
            + ", ".join(unknown_change_types)
            + ". Available: "
            + ", ".join(available_change_types(registry))
        )

    inferred_change_type_ids = infer_change_type_ids(registry, normalized_changed_files)
    selected_ids = explicit_change_type_ids or inferred_change_type_ids

    selected_change_types: list[ImpactChangeType] = []
    files_to_touch: list[str] = []
    files_to_review: list[str] = []
    tests_to_run: list[str] = []
    minimum_checks: list[str] = []

    for change_type_id in selected_ids:
        payload = known_change_types[change_type_id]
        must_touch = [str(item) for item in payload.get("must_touch", [])]
        must_review = [str(item) for item in payload.get("must_review", [])]
        recommended_tests = [str(item) for item in payload.get("recommended_tests", [])]
        selected_change_types.append(
            ImpactChangeType(
                id=change_type_id,
                description=str(payload.get("description", "")),
                reason="explicit" if change_type_id in explicit_change_type_ids else "inferred",
                matched_files=_matching_files(normalized_changed_files, must_touch + must_review),
                must_touch=must_touch,
                must_review=must_review,
                minimum_checks=[str(item) for item in payload.get("minimum_checks", [])],
                recommended_tests=recommended_tests,
            )
        )
        files_to_touch.extend(must_touch)
        files_to_review.extend(must_review)
        tests_to_run.extend(recommended_tests)
        minimum_checks.extend(str(item) for item in payload.get("minimum_checks", []))

    matched_subsystems: list[ImpactSubsystem] = []
    for subsystem in registry.get("subsystems", []):
        subsystem_files = [str(item) for item in subsystem.get("files", [])]
        matched_files = _matching_files(normalized_changed_files, subsystem_files)
        reason = ""
        if matched_files:
            reason = "matched_changed_file"
        elif selected_ids:
            selected_patterns: list[str] = []
            for change_type_id in selected_ids:
                selected_patterns.extend(known_change_types[change_type_id].get("must_touch", []))
                selected_patterns.extend(known_change_types[change_type_id].get("must_review", []))
            if _patterns_overlap(subsystem_files, [str(item) for item in selected_patterns]):
                reason = "linked_from_change_type"
        if not reason:
            continue
        matched_subsystems.append(
            ImpactSubsystem(
                id=str(subsystem["id"]),
                purpose=str(subsystem.get("purpose", "")),
                reason=reason,
                matched_files=matched_files,
                files=subsystem_files,
                tests=[str(item) for item in subsystem.get("tests", [])],
                must_review_when_changed=[str(item) for item in subsystem.get("must_review_when_changed", [])],
            )
        )
        tests_to_run.extend(str(item) for item in subsystem.get("tests", []))
        files_to_review.extend(str(item) for item in subsystem.get("must_review_when_changed", []))

    canonical_docs = [str(item) for item in registry.get("canonical_docs", [])]
    notes: list[str] = []
    if not explicit_change_type_ids and inferred_change_type_ids:
        notes.append("Типы изменений были выведены автоматически по списку измененных файлов.")
    if explicit_change_type_ids and inferred_change_type_ids:
        extra_inferred = [item for item in inferred_change_type_ids if item not in explicit_change_type_ids]
        if extra_inferred:
            notes.append(
                "По измененным файлам дополнительно совпали типы изменений: " + ", ".join(extra_inferred) + "."
            )
    unmatched_files = [
        changed_file
        for changed_file in normalized_changed_files
        if all(changed_file not in subsystem.matched_files for subsystem in matched_subsystems)
    ]
    if unmatched_files:
        notes.append("Не удалось привязать к подсистемам: " + ", ".join(unmatched_files) + ".")
    if not selected_ids:
        notes.append("Тип изменения не выбран и не выведен автоматически. Укажите --change-type или --changed-file.")

    return ImpactReport(
        project=str(registry.get("project", "")),
        registry_path=str(registry_path.resolve()),
        changed_files=normalized_changed_files,
        selected_change_types=selected_change_types,
        inferred_change_types=inferred_change_type_ids,
        matched_subsystems=matched_subsystems,
        files_to_touch=_dedupe(files_to_touch),
        files_to_review=_dedupe(files_to_review),
        tests_to_run=_dedupe(tests_to_run),
        documents_to_review=_dedupe(canonical_docs),
        minimum_checks=_dedupe(minimum_checks),
        core_invariants=[str(item) for item in registry.get("core_invariants", [])],
        notes=notes,
    )


def render_text_report(report: ImpactReport) -> str:
    lines: list[str] = []
    lines.append(f"Project: {report.project}")
    lines.append(f"Registry: {report.registry_path}")

    if report.changed_files:
        lines.append("")
        lines.append("Changed files:")
        lines.extend(f"- {item}" for item in report.changed_files)

    if report.selected_change_types:
        lines.append("")
        lines.append("Selected change types:")
        for item in report.selected_change_types:
            lines.append(f"- {item.id} [{item.reason}]")
            if item.description:
                lines.append(f"  {item.description}")
            if item.matched_files:
                lines.append(f"  matched files: {', '.join(item.matched_files)}")

    if report.matched_subsystems:
        lines.append("")
        lines.append("Matched subsystems:")
        for subsystem in report.matched_subsystems:
            lines.append(f"- {subsystem.id} [{subsystem.reason}]")
            if subsystem.purpose:
                lines.append(f"  {subsystem.purpose}")
            if subsystem.matched_files:
                lines.append(f"  matched files: {', '.join(subsystem.matched_files)}")

    if report.files_to_touch:
        lines.append("")
        lines.append("Files to update:")
        lines.extend(f"- {item}" for item in report.files_to_touch)

    if report.files_to_review:
        lines.append("")
        lines.append("Files to review:")
        lines.extend(f"- {item}" for item in report.files_to_review)

    if report.tests_to_run:
        lines.append("")
        lines.append("Tests to run:")
        lines.extend(f"- {item}" for item in report.tests_to_run)

    if report.documents_to_review:
        lines.append("")
        lines.append("Documents to review:")
        lines.extend(f"- {item}" for item in report.documents_to_review)

    if report.minimum_checks:
        lines.append("")
        lines.append("Minimum checks:")
        lines.extend(f"- {item}" for item in report.minimum_checks)

    if report.core_invariants:
        lines.append("")
        lines.append("Core invariants:")
        lines.extend(f"- {item}" for item in report.core_invariants)

    if report.notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"- {item}" for item in report.notes)

    return "\n".join(lines)
