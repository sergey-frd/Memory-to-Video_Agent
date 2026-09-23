"""Resume a configured image-edit series via the unmodified imagegen skill CLI."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext

from PIL import Image
from dotenv import load_dotenv


def digest(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(p, v):
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(p)


def check_image(p):
    with Image.open(p) as im:
        im.load()
        if im.format != 'PNG':
            raise ValueError(f'Expected PNG: {p}')
        if 'A' in im.getbands() and im.getchannel('A').getextrema() != (255, 255):
            raise ValueError(f'Non-opaque output: {p}')
        if im.width < 256 or im.height < 256:
            raise ValueError(f'Unexpected output dimensions: {p}')
        return list(im.size)


@contextmanager
def exclusive_lock(p):
    import msvcrt
    with p.open('a+b') as f:
        if p.stat().st_size == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def export_pairs(root, state, cfg):
    pairs = []
    for item in cfg['items']:
        saved = state['items'].get(item['pair_id'], {})
        if saved.get('status') == 'GENERATED_REVIEW_REQUIRED':
            p = Path(item['planned_output'])
            if digest(p) != saved['output_sha256']:
                raise ValueError(f'Output changed: {p}')
            pairs.append(dict(pair_id=item['pair_id'], source_id=item['source_id'],
                original_path=item['original_path'], original_sha256=item['original_sha256'],
                output_path=str(p), output_sha256=saved['output_sha256'],
                width=saved['dimensions'][0], height=saved['dimensions'][1],
                status=saved['status'], visual_review='PENDING'))
    write_json(root / 'actual_pairs.json', pairs)
    keys = ['pair_id', 'source_id', 'original_path', 'original_sha256', 'output_path',
            'output_sha256', 'width', 'height', 'status', 'visual_review']
    tmp = root / 'actual_pairs.csv.tmp'
    with tmp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys); writer.writeheader(); writer.writerows(pairs)
    tmp.replace(root / 'actual_pairs.csv')


def run(config, dry_run=False, env_file=None):
    cfg = json.loads(config.read_text(encoding='utf-8-sig'))
    root = Path(cfg['output_root']).resolve()
    cli = Path(cfg['imagegen_cli'])
    items = cfg['items']
    if cfg['schema_version'] != 1 or len(items) != cfg['expected_count']:
        raise ValueError('Invalid schema/count')
    for field in ('pair_id', 'source_id', 'original_path', 'planned_output'):
        if len({i[field] for i in items}) != len(items):
            raise ValueError(f'Duplicate {field}')
    if digest(cli) != cfg['imagegen_cli_sha256']:
        raise ValueError('CLI changed; revalidate config before continuing')
    for item in items:
        for pathkey, hashkey in [('original_path','original_sha256'), ('reference_path','reference_sha256'), ('prompt_path','prompt_sha256')]:
            if digest(Path(item[pathkey])) != item[hashkey]:
                raise ValueError(f'Changed {pathkey}: {item["pair_id"]}')
        if not Path(item['planned_output']).resolve().is_relative_to(root):
            raise ValueError('Output outside task root')
        check_image(Path(item['reference_path']))
        if Path(item['reference_path']).stat().st_size >= 50_000_000:
            raise ValueError('Reference exceeds upload limit; prepare separate compatible reference')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'logs').mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
    load_dotenv(Path(env_file or cfg['env_file']), override=False)
    if os.getenv('OPENAI_API_KEY'):
        env['OPENAI_API_KEY'] = os.environ['OPENAI_API_KEY']
    if not dry_run and not env.get('OPENAI_API_KEY'):
        raise ValueError('OPENAI_API_KEY is required; set it in this console or configured env_file')
    api_requests_started = 0
    # Dry-run never updates generation state or sends requests and may inspect an active job.
    with (nullcontext() if dry_run else exclusive_lock(root / 'generation.lock')):
        state_path = root / 'generation_state.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else dict(config_sha256=digest(config), items={})
        if state['config_sha256'] != digest(config):
            raise ValueError('Config changed after start; use a new output folder')
        for n, item in enumerate(items, 1):
            key = item['pair_id']; out = Path(item['planned_output'])
            saved = state['items'].get(key, {})
            if saved.get('status') == 'GENERATED_REVIEW_REQUIRED':
                if digest(out) != saved['output_sha256']:
                    raise ValueError(f'Completed output changed: {out}')
                print(f'SKIP {n}/{len(items)} {key}: verified completed output', flush=True)
                continue
            if out.exists():
                if saved.get('status') != 'REQUEST_STARTED':
                    raise ValueError(f'Untracked output, refusing overwrite: {out}')
                dims = check_image(out)
                if dry_run:
                    print(f'RECOVERABLE {key}: saved PNG exists; next run will register it', flush=True)
                    continue
                state['items'][key] = dict(status='GENERATED_REVIEW_REQUIRED', output_sha256=digest(out), dimensions=dims)
                write_json(state_path, state); export_pairs(root, state, cfg)
                continue
            if saved.get('status') == 'REQUEST_STARTED' and not dry_run:
                raise ValueError(f'Previous request outcome unknown for {key}. Inspect log before retry; no automatic duplicate API request.')
            cmd = [sys.executable, '-B', str(cli), 'edit', '--model', cfg['model'],
                   '--image', item['reference_path'], '--prompt-file', item['prompt_path'],
                   '--size', cfg['size'], '--quality', cfg['quality'], '--background', 'opaque',
                   '--output-format', 'png', '--n', '1', '--no-augment', '--out', str(out)]
            if dry_run:
                cmd.append('--dry-run')
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                state['items'][key] = dict(status='REQUEST_STARTED', started_at=time.time())
                write_json(state_path, state)
                api_requests_started += 1
            log = root / 'logs' / f'{key}_{"dry_run" if dry_run else "generation"}_{time.time_ns()}.log'
            print(f'{"CHECK" if dry_run else "GENERATE"} {n}/{len(items)} {key}; log={log}', flush=True)
            with log.open('w', encoding='utf-8') as f:
                proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=env)
                while True:
                    try:
                        rc = proc.wait(timeout=15); break
                    except subprocess.TimeoutExpired:
                        print(f'WAIT {key}: CLI still running (does not prove server progress)', flush=True)
                    except BaseException:
                        proc.terminate(); proc.wait(); raise
            if rc:
                raise RuntimeError(f'CLI exited {rc}; inspect {log}; completed outputs preserved')
            if not dry_run:
                dims = check_image(out)
                state['items'][key] = dict(status='GENERATED_REVIEW_REQUIRED', output_sha256=digest(out), dimensions=dims)
                write_json(state_path, state); export_pairs(root, state, cfg)
        status = 'VALIDATED_NO_API' if dry_run else 'GENERATED_REVIEW_REQUIRED'
        if not dry_run:
            export_pairs(root, state, cfg)
            state['status'] = status; write_json(state_path, state)
        write_json(root / ('dry_run_result.json' if dry_run else 'generation_result.json'),
                   dict(status=status, expected_count=len(items), api_called=bool(api_requests_started),
                        visual_review='NOT_PERFORMED', config_sha256=digest(config)))
        print(status, flush=True)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--env-file', type=Path, help='Runtime override; does not change the job identity')
    a = p.parse_args()
    run(a.config.resolve(), a.dry_run, a.env_file)
