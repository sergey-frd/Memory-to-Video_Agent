from utils.project_publication import _is_excluded_file_name, _is_publishable_source_file
from pathlib import Path

def test_personal_portrait_jobs_stay_out_of_public_bundle():
    for name in ["ROMANN26_NATIVE_RESULT_RU.md", "run_classify_RomanN26.bat", "prepare_romann26_watercolor_plan.py", "config_RomanN.json"]:
        assert _is_excluded_file_name(name)
    assert not _is_excluded_file_name("config_full.example.json")
    assert not _is_excluded_file_name("PORTRAIT_WORKFLOW_CHOICES_RU.md")
    root=Path(".").resolve()
    assert not _is_publishable_source_file(root/"docs/PORTRAIT_EDITORIAL_CHOICES_RU.md",root)


def test_family_runtime_and_private_recipes_stay_local():
    root = Path('.').resolve()
    for relative in ['tasks/BM26/classify/classification.json',
                     '.test_runs/private_case/scene.json', 'project_media/PROJECT/archive/plan.json',
                     'tools/bm26_closeout.py', 'scripts/sample_bm26_color.py',
                     'docs/BM26_STORAGE_20260929_RU.md', 'config_grok_queue.json']:
        assert not _is_publishable_source_file(root / relative, root)
    for relative in ['scripts/run_grok_queue.py', 'config_grok_queue.example.json',
                     'config_grok_delivery.example.json', 'docs/GROK_QUEUE_RU.md']:
        assert _is_publishable_source_file(root / relative, root)
