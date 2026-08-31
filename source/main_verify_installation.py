"""Read-only release/dependency check. Never reads API keys or local configs."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def git(*args):
    if not shutil.which("git"):
        return ""
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=20)
    return result.stdout.strip() if result.returncode == 0 else ""


def package_differences(lock: Path) -> list[str]:
    errors = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = "MISSING"
        if installed != expected:
            errors.append(f"{name}: expected {expected}, found {installed}")
    return errors


def main(argv=None):
    from utils.premiere_art_runtime import configure_stdio
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-tag", action="store_true", help="Also require the clean matching release tag")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # Public bundle keeps VERSION in the parent of source/.
    version_file = ROOT / "VERSION"
    if not version_file.is_file() and ROOT.name == "source":
        version_file = ROOT.parent / "VERSION"
    version = version_file.read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d{2}", version):
        raise ValueError("Malformed VERSION")
    errors = package_differences(ROOT / "requirements-lock-windows-py314.txt")
    runtime = f"{platform.python_version()} {platform.machine()} {platform.system()}"
    if runtime != "3.14.2 AMD64 Windows":
        errors.append("Expected Python 3.14.2 AMD64 Windows; found " + runtime)
    head = git("rev-parse", "HEAD")
    tags = git("tag", "--points-at", "HEAD").splitlines()
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if args.require_tag and ("v" + version not in tags or not head or dirty):
        errors.append("Checkout must be clean and exactly at v" + version)
    try:
        from utils.video_frame_extract import resolve_ffmpeg_executable
        ffmpeg = resolve_ffmpeg_executable()
        result = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError("ffmpeg -version failed")
        ffmpeg_version = result.stdout.splitlines()[0]
    except Exception as exc:
        ffmpeg_version = str(exc)
        errors.append("FFmpeg not executable: " + str(exc))
    report = {"status": "PASS" if not errors else "FAIL", "version": version, "git_head": head,
              "tags": tags, "tracked_changes": bool(dirty), "python": runtime,
              "ffmpeg": ffmpeg_version, "errors": errors,
              "external_checks": "Install/check Adobe Premiere, plugins, browser login and relink media separately. This check does not open Premiere."}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
          "\n".join([f"{report['status']}: release {version}", f"Python: {runtime}",
                     f"Git: {head or 'unavailable'}; tags: {', '.join(tags) or 'none'}", ffmpeg_version,
                     *errors, report["external_checks"]]))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
