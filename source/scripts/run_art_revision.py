"""Execute an explicitly approved, frozen ART_2 selection; never rerun uncertain requests."""
import argparse
import copy
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageOps
from family_contract import ROOT, read, sha, task_path, verify
from ingest import write_json
from art import verified_copy, waiting, now
from api import openai_image as backend
from utils.image_analysis import analyze_image


def validate_result(output):
    geometry = read(output.with_suffix('.geometry.json'))
    verify({str(output): geometry['output_sha256'],
            str(output.with_suffix('.response.png')): geometry['raw_response_sha256'],
            str(output.with_suffix('.reference.png')): geometry['reference_sha256']})
    if geometry['postprocess_resize'] or geometry['policy'] != backend.GEOMETRY_POLICY:
        raise ValueError('Incorrect output geometry policy')
    with Image.open(output) as saved, Image.open(output.with_suffix('.response.png')) as raw:
        expected = ImageOps.exif_transpose(raw).convert('RGBA')
        if saved.size != expected.size or ImageChops.difference(saved.convert('RGB'), expected.convert('RGB')).getbbox():
            raise ValueError('Saved ART differs geometrically from API response')
        return dict(new_width=saved.width, new_height=saved.height,
                    new_aspect_ratio=saved.width/saved.height, new_sha256=sha(output))


def run(task, dry_run=False):
    work = task/'art_2'; plan_path = work/'ART_2_REGENERATE.json'; plan = read(plan_path)
    cfg = read(task/'task.json'); root = Path(cfg['classify']['permanent_project_dir']).resolve()
    target = Path(plan['target']).resolve()
    if target != root/'ART_2' or not target.is_dir() or plan['task_id'] != task.name:
        raise ValueError('Unexpected ART_2 destination/task')
    verify({str(root/'ART/art_manifest.json'): plan['old_manifest_sha256'],
            str(work/'assessment.json'): plan['assessment_sha256']})
    protected = read(work/'diagnostic/protected_files_before.json'); verify(protected)
    selected = [e for e in plan['entries'] if e['decision']=='REGENERATE_PORTRAIT']
    if len(selected) != plan['counts']['estimated_paid_generations']:
        raise ValueError('Selection count changed')
    for entry in selected:
        if entry['source_orientation']!='PORTRAIT' or entry['geometry_policy']!=backend.GEOMETRY_POLICY:
            raise ValueError('Invalid selected geometry')
        dest = Path(entry['new_art_path']).resolve()
        if not dest.is_relative_to(target) or dest.parent.name != entry['art_id']:
            raise ValueError('Unsafe result destination')
        verify({entry['source_path']:entry['source_sha256'],entry['old_art_path']:entry['old_art_sha256']})
    state_path = work/'generation_state.json'
    state = read(state_path) if state_path.exists() else dict(task_id=task.name,
        plan_sha256=sha(plan_path), status='APPROVED', approval='User: Продолжай. Approved four-item ART_2 plan.',
        approved_at=now(), api_requests_started=0, results={})
    if state['plan_sha256'] != sha(plan_path):raise ValueError('Frozen revision plan changed')
    print('ART_2 selected: '+', '.join(e['art_id'] for e in selected),flush=True)
    if dry_run:
        print('DRY_RUN PASS; no API, no writes',flush=True);return
    approval=read(work/'execution_request.json')
    if not approval.get('explicit_consent') or approval['selected_ids'] != [e['art_id'] for e in selected] or approval['planned_paid_generations'] != len(selected):
        raise ValueError('Explicit consent for this frozen selection is required')
    state['approval']=approval['explicit_consent']
    lock=work/'generation.lock'
    with lock.open('x',encoding='utf-8') as stream:stream.write(now())
    def save():
        state['updated']=now();write_json(state_path,state);verified_copy(state_path,target/state_path.name)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT/'.env')
        backend._CLIENT=backend._get_client().with_options(timeout=600,max_retries=0)
        save()
        for n,entry in enumerate(selected,1):
            ident=entry['art_id']; previous=state['results'].get(ident)
            output=work/'results'/entry['art_type']/ident/Path(entry['new_art_path']).name
            dest=Path(entry['new_art_path'])
            if previous and previous['status']=='COMPLETE':
                verify(previous['artifacts']);print(f'[{n}/{len(selected)}] {ident} SKIP VERIFIED',flush=True);continue
            if previous or output.exists() or dest.exists() or output.with_suffix('.response.png').exists():
                raise ValueError('Partial/untracked result; diagnose without another API request: '+ident)
            print(f'[{n}/{len(selected)}] {ident} GENERATING; model={entry["model"]}',flush=True)
            state['status']='RUNNING';state['current']=ident
            state['results'][ident]=dict(status='GENERATING',started=now())
            state['api_requests_started']+=1;save()
            with waiting(ident,10):
                backend.edit_image_with_openai(Path(entry['source_path']),entry['art_type'],output,
                    analyze_image(Path(entry['source_path'])),ident,prompt_override=entry['prompt'],model_name=entry['model'])
            facts=validate_result(output);files={}
            for suffix in ['.png','.reference.png','.response.png','.geometry.json']:
                source=output.with_suffix(suffix);remote=dest.with_suffix(suffix)
                if remote.exists():raise FileExistsError('Refusing overwrite: '+str(remote))
                verified_copy(source,remote);files[str(source)]=sha(source);files[str(remote)]=sha(remote)
            state['results'][ident]=dict(status='COMPLETE',completed=now(),artifacts=files,
                local_art_path=str(output),new_art_path=str(dest),visual_qa='PENDING',**facts)
            save();print(f'[{n}/{len(selected)}] {ident} COMPLETE — response geometry and permanent copy verified',flush=True)
        verify(protected)
        manifest=copy.deepcopy(plan)
        manifest.update(generation_authorized=True,status='GENERATED — VISUAL QA PENDING',timestamp=now(),
                        api_requests_started=state['api_requests_started'],protected_files_verified=len(protected))
        for entry in manifest['entries']:
            result=state['results'].get(entry['art_id'])
            if result:entry.update(result)
            else:entry['status']='SKIPPED'
        write_json(work/'art_manifest.json',manifest);verified_copy(work/'art_manifest.json',target/'art_manifest.json')
        state['status']='GENERATED — VISUAL QA PENDING';save();print(state['status'],flush=True)
    except BaseException as exc:
        state.update(status='FAILED',error=f'{type(exc).__name__}: {exc}',retry_policy='No automatic repeat of uncertain paid request')
        save();raise
    finally:
        lock.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('task');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();run(task_path(args.task),args.dry_run)
