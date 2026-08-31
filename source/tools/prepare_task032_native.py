"""Copy native executors into the configured task folder, without launching Adobe."""
from pathlib import Path
import json


def prepare(settings):
    destination = settings["OUT"]
    sources = Path(__file__).resolve().parents[1] / "premiere_scripts" / "task032"
    payloads = {p.name: p.read_bytes() for p in sources.glob("*.jsx")}
    payloads["TASK_032_RUNTIME.json"] = (json.dumps({"source_project": str(settings["SOURCE"]),
        "source_sequence": settings["NAME"]}, indent=2) + "\n").encode("utf-8")
    if len(payloads) < 2:
        raise FileNotFoundError("Native script templates are missing")
    for name, content in payloads.items():
        target = destination / name
        if target.exists() and target.read_bytes() != content:
            raise FileExistsError("Existing native script/config differs: " + str(target))
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in payloads.items():
        target = destination / name
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(content)
    return {"status": "NATIVE_SCRIPTS_PREPARED_NOT_EXECUTED", "directory": str(destination), "files": sorted(payloads)}
