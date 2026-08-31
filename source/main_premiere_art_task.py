"""Safe launcher for task-specific ART 031–034 workflows (no implicit edit)."""
from __future__ import annotations
import argparse
import importlib
import json
import os
from pathlib import Path
from utils.premiere_art_runtime import ENV, load_profile, check_profile, assert_fresh_outputs, configure_stdio

MODULES = {"031": "main_premiere_task_031_art_final", "033": "main_premiere_task_033_fit_pulse_fill",
           "034": "main_premiere_task_034_single_soft_impulse"}


def main(argv=None):
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=["031", "032", "033", "034"], required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stage", choices=["check", "run", "preflight", "plan", "dry-run", "apply", "prepare-native"], default="check")
    parser.add_argument("--execute", action="store_true", help="Explicitly allow the selected stage to write files")
    args = parser.parse_args(argv)
    if not __debug__:
        parser.error("Python -O is unsupported; assertions are part of the task validation")
    allowed = {"check", "preflight", "plan", "dry-run", "apply", "prepare-native"} if args.task == "032" else {"check", "run"}
    if args.stage not in allowed:
        parser.error(f"Stage {args.stage} is not implemented for task {args.task}")
    settings = load_profile(args.config, args.task)
    if args.stage == "check":
        result = check_profile(settings, args.task)
    else:
        if not args.execute:
            parser.error("Stages that write artifacts require --execute (including plans and reports)")
        check_profile(settings, args.task)
        os.environ[ENV] = str(args.config.resolve())
        if args.task != "032":
            assert_fresh_outputs(settings, args.task)
            result = importlib.import_module(MODULES[args.task]).main()
        elif args.stage == "prepare-native":
            from tools.prepare_task032_native import prepare
            result = prepare(settings)
        elif args.stage == "preflight":
            result = importlib.import_module("tools.task032_preflight").main()
        else:
            module = importlib.import_module("tools.task032_pipeline")
            if args.stage == "plan":
                result = module.plan()
            else:
                master = json.loads((settings["OUT"] / "TASK_032_MASTER.json").read_text(encoding="utf-8"))
                if args.stage == "dry-run":
                    if not module.dryrun(master):
                        raise SystemExit(1)
                    result = {"status": "DRY_RUN_PASS"}
                else:
                    result = module.apply(master)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
