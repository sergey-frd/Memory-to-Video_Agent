"""Sequential, resumable coordinator for the existing video workflow scripts."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_generation_config
from tools.prepare_classification_input import collect, digest, write_json
from utils.premiere_project import load_premiere_project_root, find_project_sequence_node

REPO = Path(__file__).resolve().parents[1]
STAGES = ['inventory', 'classification', 'structure', 'draft', 'native']
SCRIPTS = ['prepare_classification_input.py', 'classify_source_package.py',
           'build_video_structure.py', 'render_structure_draft.py', 'prepare_native_export.py']
RESULTS = ['classification_input.json', 'classification_result.json', 'structure_result.json',
           'render_result.json', 'preparation_result.json']
SUCCESS = ['READY', 'CLASSIFIED_REVIEW_REQUIRED', 'DRAFT_REVIEW_REQUIRED', 'DRAFT_READY', 'PREPARED_NATIVE_NOT_RUN']


def validate_config(cfg):
    if cfg.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    for key in ('task_id', 'hero_config', 'project', 'source_sequence', 'target_sequence', 'output_root'):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip() or '<' in cfg[key]:
            raise ValueError(f'Fill configuration field: {key}')
    if type(cfg.get('target_duration_seconds')) is not int or cfg['target_duration_seconds'] <= 0:
        raise ValueError('Positive target_duration_seconds required')
    if cfg.get('duration_mode', 'compact') not in ('compact', 'target', 'coverage'):
        raise ValueError('Invalid duration_mode')
    if type(cfg.get('ai_enabled', True)) is not bool:
        raise ValueError('ai_enabled must be boolean')


def run(path, check=False, until='draft'):
    path = path.resolve()
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    validate_config(cfg)
    resolve = lambda value: (path.parent / value).resolve()
    hero, project, root = resolve(cfg['hero_config']), resolve(cfg['project']), resolve(cfg['output_root'])
    load_generation_config(hero)
    tree = load_premiere_project_root(project)
    for name in (cfg['source_sequence'], cfg['target_sequence']):
        if find_project_sequence_node(tree, name) is None:
            raise ValueError('Sequence not found: ' + name)
    if until == 'native' and not resolve(cfg['native_preset']).is_file():
        raise ValueError('Native export preset missing')
    fingerprint = hashlib.sha256(json.dumps(dict(config=cfg, hero=digest(hero), project=digest(project)),
                                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if check:
        rows = collect(project, cfg['source_sequence'], hash_media=False)
        print(f'MEDIA PRECHECK PASS: {len(rows)} unique visual sources; paths and formats supported. Decode not checked.')
        print('PRECHECK PASS. No AI, render or project changes. Stages: ' + ', '.join(STAGES[:STAGES.index(until)+1]))
        return
    root.mkdir(parents=True, exist_ok=True)
    lock = root / 'pipeline.lock'
    # Exclusive file prevents two launchers writing checkpoints for one task.
    with lock.open('x', encoding='utf-8') as f:
        f.write('Pipeline running. Remove this lock only after confirming the process has stopped.')
    try:
        state_path = root / 'pipeline_state.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else dict(fingerprint=fingerprint, stages={})
        if state['fingerprint'] != fingerprint:
            raise ValueError('Configuration/hero/project changed. Use a new output_root to preserve the previous run.')
        def save():
            temp = state_path.with_suffix('.tmp'); write_json(temp, state); temp.replace(state_path)
        def result(stage):
            return Path(state['stages'][stage]['result'])
        configs = root / 'stage_configs'; configs.mkdir(exist_ok=True)
        for i, stage in enumerate(STAGES[:STAGES.index(until)+1]):
            previous = state['stages'].get(stage)
            if previous:
                for name, sha in previous['artifacts'].items():
                    if digest(name) != sha:
                        raise ValueError(f'Completed stage artifact changed: {name}')
                print(f'[SKIP] {stage}: checkpoint verified', flush=True)
                continue
            outroot = root / stage
            base = dict(schema_version=1, output_root=str(outroot))
            if stage == 'inventory':
                base.update(task_id=cfg['task_id'], hero_config=str(hero), project=str(project),
                            source_sequence=cfg['source_sequence'], target_sequence=cfg['target_sequence'])
            elif stage == 'classification':
                base.update(input_manifest=str(result('inventory')), ai_enabled=cfg.get('ai_enabled', True), model=cfg['model'])
            elif stage == 'structure':
                base.update(classification_result=str(result('classification')), ai_enabled=cfg.get('ai_enabled', True),
                            model=cfg['model'], duration_mode=cfg.get('duration_mode', 'compact'), target_duration_seconds=cfg['target_duration_seconds'],
                            narrative=cfg['narrative'], required_ids=cfg.get('required_ids', []), excluded_ids=cfg.get('excluded_ids', []))
            elif stage == 'draft':
                base.update(structure=str(result('structure').parent / 'structure.json'), width=1280, height=720,
                            fps=25, video_bitrate='1400k', audio_bitrate='128k')
            else:
                base.update(project=str(project), target_sequence=cfg['target_sequence'],
                            edit_plan=str(result('draft').parent / 'edit_plan.json'),
                            preset=str(resolve(cfg['native_preset'])), width=1920, height=1080)
            stage_config = configs / (stage + '.local.json'); write_json(stage_config, base)
            existing = set(outroot.glob('*/' + RESULTS[i]))
            logfile = root / (stage + '_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.log')
            print(f'[START] {stage}; log={logfile}', flush=True)
            state.update(status='RUNNING', current_stage=stage); save()
            with logfile.open('w', encoding='utf-8') as log:
                proc = subprocess.Popen([sys.executable, '-B', '-u', str(REPO / 'tools' / SCRIPTS[i]),
                    '--config', str(stage_config)], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace')
                try:
                    for line in proc.stdout:
                        print(line, end='', flush=True); log.write(line); log.flush()
                    code = proc.wait()
                except BaseException:
                    proc.terminate(); proc.wait(); raise
            if code:
                state.update(status='FAILED', failed_stage=stage); save()
                raise RuntimeError(f'{stage} failed with code {code}; see {logfile}')
            created = set(outroot.glob('*/' + RESULTS[i])) - existing
            if len(created) != 1:
                raise ValueError(f'{stage}: expected exactly one new result')
            artifact = created.pop()
            data = json.loads(artifact.read_text(encoding='utf-8'))
            if data['status'] != SUCCESS[i]:
                state.update(status='NEEDS_REVIEW', current_result=str(artifact)); save()
                print(f'[STOP] {stage}: {data["status"]}. Manual input required.'); return
            extras = {'inventory':['inventory.json'], 'classification':['catalog.jsonl'],
                      'structure':['structure.json'], 'draft':['edit_plan.json','draft_720p.mp4'],
                      'native':['job.json','assemble_export.jsx']}[stage]
            paths = [artifact] + [artifact.parent / name for name in extras]
            state['stages'][stage] = dict(result=str(artifact), artifacts={str(p):digest(p) for p in paths})
            save(); print('[DONE] ' + stage, flush=True)
        state.update(status='PREPARED_NATIVE_NOT_RUN' if until=='native' else 'STOPPED_AFTER_'+until.upper())
        save(); print(f'[FINISH] {state["status"]}; {state_path}')
    finally:
        lock.unlink()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--check',action='store_true')
    p.add_argument('--until',choices=STAGES,default='draft')
    a=p.parse_args();run(a.config,a.check,a.until)
