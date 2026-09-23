from utils.project_publication import _is_excluded_file_name, _is_publishable_source_file
from pathlib import Path

def test_personal_portrait_jobs_stay_out_of_public_bundle():
    for name in ["ROMANN26_NATIVE_RESULT_RU.md", "run_classify_RomanN26.bat", "prepare_romann26_watercolor_plan.py", "config_RomanN.json"]:
        assert _is_excluded_file_name(name)
    assert not _is_excluded_file_name("config_full.example.json")
    assert not _is_excluded_file_name("PORTRAIT_WORKFLOW_CHOICES_RU.md")
    root=Path(".").resolve()
    assert not _is_publishable_source_file(root/"docs/PORTRAIT_EDITORIAL_CHOICES_RU.md",root)
