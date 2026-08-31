"""Local path configuration and safe entry points for the fixed ART contracts.

These profiles relocate a known project; they do not retarget its artistic plan.
No project or report is written when a module is imported or checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ENV = "PREMIERE_ART_CONFIG"
PATH_KEYS = {
    "031": "SOURCE_PROJECT BACKUP_PROJECT OUTPUT_PROJECT CHECKPOINT_PROJECT PREVIEW_PATH REPO_TASK_DIR LOCAL_TASK_DIR".split(),
    "032": "SOURCE DEST CHECKPOINT BACKUP PREVIEW OUT REPORT_DIR".split(),
    "033": "SOURCE_PROJECT BACKUP_PROJECT OUTPUT_PROJECT COLOR_PREVIEW FINAL_PREVIEW COMPARISON_PREVIEW REPO_DIR LOCAL_DIR".split(),
    "034": "SOURCE_PROJECT OUTPUT_PROJECT PREVIEW COMPARISON OLD_PREVIEW REPO_DIR LOCAL_DIR TASK033_PLAN".split(),
}
TEXT_KEYS = {
    "031": "SOURCE_SEQUENCE OUTPUT_SEQUENCE".split(),
    "032": "NAME TARGET BG SHA".split(),
    "033": "SOURCE_SEQUENCE COLOR_SEQUENCE FINAL_SEQUENCE".split(),
    "034": "SOURCE_SEQUENCE BAD_REF_SEQUENCE OUTPUT_SEQUENCE".split(),
}
INPUT_KEYS = {"SOURCE_PROJECT", "SOURCE", "OLD_PREVIEW", "TASK033_PLAN"}
DIR_KEYS = {"REPO_TASK_DIR", "LOCAL_TASK_DIR", "OUT", "REPORT_DIR", "REPO_DIR", "LOCAL_DIR"}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_profile(path: Path, task: str) -> dict:
    path = path.resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("task") != task or payload.get("schema_version") != 1:
        raise ValueError("Expected schema_version=1 and task=" + task)
    settings = payload.get("settings", {})
    required = set(PATH_KEYS[task] + TEXT_KEYS[task])
    if set(settings) != required:
        raise ValueError(f"Config keys: missing={sorted(required-set(settings))}, unknown={sorted(set(settings)-required)}")
    if any(not isinstance(v, str) or not v.strip() or "<" in v for v in settings.values()):
        raise ValueError("All settings must contain nonempty, resolved values")
    resolved = dict(settings)
    for key in PATH_KEYS[task]:
        item = Path(os.path.expandvars(settings[key])).expanduser()
        resolved[key] = (path.parent / item).resolve() if not item.is_absolute() else item.resolve()
    files = [resolved[k] for k in PATH_KEYS[task] if k not in DIR_KEYS]
    if len(set(files)) != len(files):
        raise ValueError("Input, backup and output files must have distinct paths")
    directories = [resolved[k] for k in DIR_KEYS.intersection(resolved)]
    if len(set(directories)) != len(directories):
        raise ValueError("Working and delivery directories must have distinct paths")
    if task == "032" and (len(resolved["SHA"]) != 64 or any(c not in "0123456789abcdef" for c in resolved["SHA"])):
        raise ValueError("SHA must be the exact 64-character source SHA256")
    return resolved


def configure_module(namespace: dict, task: str) -> None:
    path = os.environ.get(ENV)
    if not path:
        return  # Import is read-only. CLI always requires an explicit profile.
    settings = load_profile(Path(path), task)
    namespace.update({k: v for k, v in settings.items() if k in namespace})
    if task == "034" and "SOURCE_CANDIDATES" in namespace:
        namespace["SOURCE_CANDIDATES"] = [settings["SOURCE_PROJECT"]]
    if "DRIVE_CANDIDATES" in namespace:
        namespace["DRIVE_CANDIDATES"] = []  # Delivery is a separate, explicit operation.


def assert_fresh_outputs(settings: dict, task: str) -> None:
    existing = [str(settings[k]) for k in PATH_KEYS[task]
                if k not in INPUT_KEYS and k not in DIR_KEYS and settings[k].exists()]
    if existing:
        raise FileExistsError("Existing output/backup will not be overwritten: " + "; ".join(existing))
    for key in DIR_KEYS.intersection(settings):
        directory = settings[key]
        if directory.exists() and any(directory.iterdir()):
            raise FileExistsError("Choose an empty reports directory: " + str(directory))


def require_fresh_run(task: str) -> None:
    if not __debug__ or not os.environ.get(ENV):
        raise RuntimeError("Use the configured ART launcher without Python -O")
    assert_fresh_outputs(load_profile(Path(os.environ[ENV]), task), task)


def check_profile(settings: dict, task: str) -> dict:
    from utils.premiere_project import (load_premiere_project_root, find_project_sequence_node,
        build_project_object_id_lookup, PREMIERE_TICKS_PER_SECOND)
    from utils.premiere_sequence_motion import _video_settings
    source = settings["SOURCE" if task == "032" else "SOURCE_PROJECT"]
    root = load_premiere_project_root(source)
    name = settings["NAME" if task == "032" else "SOURCE_SEQUENCE"]
    seq = find_project_sequence_node(root, name)
    if seq is None:
        raise ValueError("Source sequence missing: " + name)
    video = _video_settings(seq, build_project_object_id_lookup(root))
    if int(video["frame_rate"]) != PREMIERE_TICKS_PER_SECOND // 25:
        raise ValueError("These fixed ART contracts require 25 fps")
    if [int(x) for x in video["frame_rect"].split(",")] != [0, 0, 3840, 2160]:
        raise ValueError("These fixed ART contracts require a 3840x2160 sequence")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if task == "032" and digest != settings["SHA"]:
        raise ValueError("Source SHA256 does not match the profile")
    return {"status": "INPUT_CHECK_PASS", "task": task, "source": str(source),
            "sequence": name, "source_sha256": digest, "video_settings": video,
            "project_files_written": False,
            "note": "Input check only; not a dry-run or Premiere Desktop/media/effect QA."}


def tool_entry(task: str, function, argv=None) -> None:
    """Guard low-level helpers; import and --help never execute their body."""
    configure_stdio()
    parser = argparse.ArgumentParser(description=function.__module__ + ": fixed ART task helper")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Allow this helper to write its task artifacts")
    parser.add_argument("--preview", type=Path, help="QA preview override (final_qa only)")
    parser.add_argument("--scopes-only", action="store_true", help="Omit comparison export (scopes_compare only)")
    parser.add_argument("--preset", type=Path, help="Installed Adobe H.264 .epr preset (make_preset only)")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("This helper requires --execute; use main_premiere_art_task.py --stage check for read-only validation")
    if not __debug__:
        parser.error("Python -O is unsupported: validation assertions must remain enabled")
    load_profile(args.config, task)
    os.environ[ENV] = str(args.config.resolve())
    # Helpers are loaded only after configuration; callers pass a deferred callable.
    function(args)
