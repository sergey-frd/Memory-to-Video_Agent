#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGEN = Path(r"<LOCAL_PATH>")
PY = ROOT / ".venv" / "Scripts" / "python.exe"
MAIN = ROOT / "main_video_prompt_composer.py"


def make_v2only(src: Path, dst: Path, instruction: str) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    data["max_prompt_chars"] = 3600
    variant = next(v for v in data["scenario_variants"] if v["variant_id"] == "Variant_2")
    variant["instruction"] = instruction
    data["scenario_variants"] = [variant]
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_job(config_path: Path, *, attempts: int = 6, pause_seconds: int = 20) -> None:
    print(f"=== {config_path.name} ===")
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(pause_seconds)
        print(f"attempt {attempt}")
        result = subprocess.run(
            [str(PY), "-u", str(MAIN), "--config-file", str(config_path)],
            cwd=ROOT,
        )
        if result.returncode == 0:
            return
    raise SystemExit(f"Composer failed for {config_path}")


def main() -> int:
    make_v2only(
        REGEN / "video_prompt_config_sveta_birthday_lyrical.json",
        REGEN / "video_prompt_config_sveta_birthday_lyrical_v2only.json",
        (
            "Poetic lyrical alternative with reflective pauses and intimate warmth. "
            "Eye-level medium shots only; never bird's-eye, drone, aerial, high above, "
            "tiny figures, or distant specks. No dissolve-heavy slideshow."
        ),
    )
    make_v2only(
        REGEN / "video_prompt_config_sveta_birthday_fun.json",
        REGEN / "video_prompt_config_sveta_birthday_fun_v2only.json",
        (
            "Dance-and-song alternative: heroine sings and dances with group joy at table "
            "and on the road. Snap cuts only. Never bird's-eye, drone, aerial, tiny, "
            "or distant specks."
        ),
    )

    jobs = [
        REGEN / "video_prompt_config_sveta_birthday_lyrical_v2only.json",
        REGEN / "video_prompt_config_sveta_birthday_fun.json",
        REGEN / "video_prompt_config_sveta_birthday_fun_v2only.json",
    ]
    for index, job in enumerate(jobs):
        run_job(job)
        if index + 1 < len(jobs):
            time.sleep(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
