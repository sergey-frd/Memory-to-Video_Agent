"""Prepare one Premiere project / one JSX / one export, using existing stage code.

Preparation is offline. Native execution intentionally requires opening the copy.
After a failed native run, prepare a fresh package; never stack effects on retry.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_classification_input import digest, write_json
from tools.prepare_native_export import JSX, prepare as native_precheck
from tools.prepare_plan_animation import ANIMATE, motion_keys
from tools.prepare_plan_transitions import APPLY, select_transitions
from tools.prepare_plan_color import COLOR, global_values, values
from tools.run_video_workflow import read, verify


HELPERS = r'''
var checkpoints=[];
function signature(s){
 var parts=[s.sequenceID,s.frameSizeHorizontal,s.frameSizeVertical,s.timebase,s.end];
 for(var g=0;g<2;g++){
  var tracks=g===0?s.videoTracks:s.audioTracks;
  for(var t=0;t<tracks.numTracks;t++){
   var tr=tracks[t];parts.push(g,t,tr.clips.numItems);
   if(g===0){
    parts.push(tr.transitions.numItems);
    for(var ti=0;ti<tr.transitions.numItems;ti++)parts.push(tr.transitions[ti].start.ticks,tr.transitions[ti].end.ticks);
   }
   for(var c=0;c<tr.clips.numItems;c++){
    var clip=tr.clips[c];parts.push(clip.projectItem.getMediaPath(),clip.start.ticks,clip.end.ticks,clip.inPoint.ticks,clip.outPoint.ticks);
    for(var co=0;co<clip.components.numItems;co++){
     var component=clip.components[co];parts.push(component.matchName);
     if(component.matchName!=='AE.ADBE Motion' && component.matchName!=='AE.ADBE Lumetri')continue;
     for(var pi=0;pi<component.properties.numItems;pi++){
      var prop=component.properties[pi];parts.push(prop.displayName,String(prop.getValue()));
      if(prop.areKeyframesSupported() && prop.isTimeVarying()){
       var keys=prop.getKeys();parts.push(keys?keys.length:0);
       if(keys)for(var ki=0;ki<keys.length;ki++)parts.push(keys[ki].ticks,String(prop.getValueAtTime(keys[ki])));
      }
     }
    }
   }
  }
 }
 return parts.join('|');
}
function verifyCheckpoints(){
 for(var x=0;x<checkpoints.length;x++)if(signature(checkpoints[x].sequence)!==checkpoints[x].signature)
  throw Error('Previous checkpoint changed: '+checkpoints[x].sequence.name);
}
function cloneNamed(source,name){
 var before={},n=app.project.sequences.numSequences;
 for(var x=0;x<n;x++)before[String(app.project.sequences[x].sequenceID)]=true;
 if(!source.clone())throw Error('Sequence clone failed: '+name);
 var found=null;
 for(var x=0;x<app.project.sequences.numSequences;x++){
  var candidate=app.project.sequences[x];
  if(!before[String(candidate.sequenceID)]){
   if(found)throw Error('Ambiguous cloned sequence');found=candidate;
  }
 }
 if(!found || app.project.sequences.numSequences!==n+1)throw Error('Clone not found');
 found.name=name;
 if(found.name!==name)throw Error('Sequence rename failed');
 if(!app.project.openSequence(found.sequenceID))throw Error('Cannot activate clone');
 return found;
}
function checkpoint(){
 validateTimeline();verifyCheckpoints();
 step('save checkpoint '+seq.name);
 var started=new Date().getTime(),saved=app.project.save();
 log('SAVE returned='+String(saved)+' type='+typeof saved);
 // Hosts differ in return type. Confirm the actual disk write rather than
 // treating every non-numeric-zero result as an error.
 var disk=new File(job.project);
 if(!disk.exists || disk.length<=0 || !disk.modified || disk.modified.getTime()<started-2000)
  throw Error('Project save not confirmed on disk; return='+String(saved));
 checkpoints.push({sequence:seq,signature:signature(seq)});
 state('CHECKPOINT '+seq.name);
}
'''


def replace_once(text, marker, replacement):
    if text.count(marker) != 1:
        raise ValueError('Native stage template changed: ' + marker)
    return text.replace(marker, replacement, 1)


def build_script(job):
    start = " step('verify all final video placements');"
    end = " step('activate sequence');"
    if JSX.count(start) != 1 or JSX.count(end) != 1:
        raise ValueError('Native validation template changed')
    validation = JSX[JSX.index(start):JSX.index(end)]
    helpers = HELPERS + '\nfunction validateTimeline(){\n' + validation + '\n}\n'
    helpers += '\nfunction applyMotion(){\n' + ANIMATE + '\n}\n'
    helpers += '\nfunction applyTransitions(){\n' + APPLY + '\n}\n'
    helpers += '\nfunction applyColor(){\n' + COLOR + '\n}\n'
    script = replace_once(JSX, 'try{', helpers + '\ntry{')
    # Before any clone/mutation, reject old checkpoint names from a previous run.
    guard = r'''
 for(var ni=0;ni<job.checkpoint_names.length;ni++)
  for(var si=0;si<app.project.sequences.numSequences;si++)
   if(app.project.sequences[si].name===job.checkpoint_names[ni])throw Error('Checkpoint already exists; prepare a fresh package: '+job.checkpoint_names[ni]);
 if(job.transitions.length || job.color.length){
  app.enableQE();
  if(job.transitions.length && !qe.project.getVideoTransitionByName('Cross Dissolve'))throw Error('Cross Dissolve unavailable');
  if(job.color.length && !qe.project.getVideoEffectByName('Lumetri Color'))throw Error('Lumetri unavailable');
 }
 checkpoints.push({sequence:seq,signature:signature(seq)});
 seq=cloneNamed(seq,job.checkpoint_names[0]);
'''
    script = replace_once(script, " step('get sequence settings');", guard + "\n step('get sequence settings');")
    finish = r'''
 checkpoint();
 step('clone motion checkpoint');seq=cloneNamed(seq,job.checkpoint_names[1]);
 if(job.motion.length)applyMotion();checkpoint();
 step('clone transition checkpoint');seq=cloneNamed(seq,job.checkpoint_names[2]);
 if(job.transitions.length)applyTransitions();checkpoint();
 step('clone final color checkpoint');seq=cloneNamed(seq,job.checkpoint_names[3]);
 if(job.color.length)applyColor();checkpoint();
 job.sequence=seq.name;
'''
    script = replace_once(script, end, finish + '\n' + end)
    if len(job['checkpoint_names']) == 3:
        script = replace_once(script, " if(job.transitions.length)applyTransitions();checkpoint();\n step('clone final color checkpoint');seq=cloneNamed(seq,job.checkpoint_names[3]);", " if(job.transitions.length)applyTransitions();validateTimeline();verifyCheckpoints();")
    script = script.replace('native_progress.log', 'finish_progress.log').replace('native_status.txt', 'finish_status.txt')
    if script.count('exportAsMediaDirect(') != 1:
        raise ValueError('Expected exactly one final native export')
    return script.replace('__JOB__', json.dumps(job, ensure_ascii=True))


def effect_plan(plan, cfg):
    motion_cfg = cfg['animation']
    motion = [dict(index=i, id=c['id'], keys=motion_keys(c['frames'], motion_cfg['peak_fraction'],
               motion_cfg['zoom_fraction'], motion_cfg['return_fraction']))
              for i, c in enumerate(plan['clips']) if c['kind'] == 'image'] if motion_cfg['enabled'] else []
    transitions, skipped = select_transitions(plan, cfg['transitions']) if cfg['transitions']['enabled'] else ([], [])
    if cfg['transitions']['enabled'] and 'cut_frames' in cfg['transitions']:
        requested = cfg['transitions']['cut_frames']
        if any(type(n) is not int for n in requested) or len(set(requested)) != len(requested):
            raise ValueError('Transition cut_frames must be unique integers')
        eligible = {s['cut_frame'] for s in transitions}
        if set(requested) - eligible:
            raise ValueError('Requested transitions lack verified handles or eligible clips: ' + str(set(requested)-eligible))
        skipped += [dict(s, reason='not selected by editor') for s in transitions if s['cut_frame'] not in requested]
        transitions = [s for s in transitions if s['cut_frame'] in requested]
    color_cfg = cfg['color']
    color = []
    look = global_values(color_cfg['global_look']) if color_cfg['enabled'] else {}
    overrides = color_cfg.get('overrides', {})
    if set(overrides) - {c['id'] for c in plan['clips']}:
        raise ValueError('Unknown color override ID')
    if color_cfg['enabled']:
        default = values(color_cfg.get('default_correction', {}))
        for i, c in enumerate(plan['clips']):
            correction = dict(default); correction.update(values(overrides.get(c['id'], {})))
            color.append(dict(index=i, instance=c.get('clip_instance_id', c['id']), correction=correction))
    return dict(motion=motion, transitions=transitions, color=color, global_look=look), skipped


def prepare(path, dry_run=False):
    path = Path(path).resolve(); cfg = read(path)
    if cfg.get('schema_version') != 1:
        raise ValueError('Expected schema_version 1')
    local = lambda name: (path.parent / cfg[name]).resolve()
    names = cfg['checkpoint_names']
    if len(names) not in (3,4) or any(not isinstance(s, str) or not s.strip() for s in names) or len(set(names)) != len(names):
        raise ValueError('Three or four distinct checkpoint names required')
    if cfg['target_sequence'] in names:
        raise ValueError('Keep the empty source sequence separate')
    if (cfg['width'], cfg['height']) not in ((1920, 1080), (3840, 2160)):
        raise ValueError('Supported profiles: 1920x1080 or 3840x2160 (UHD 4K)')
    state = read(local('workflow_state'))
    index = cfg['review_index']
    if type(index) is not int or not 1 <= index <= len(state['reviews']):
        raise ValueError('Invalid review_index')
    review = state['reviews'][index-1]; verify(review['artifacts'])
    if Path(review['plan']).resolve() != local('edit_plan'):
        raise ValueError('Review does not match the configured edit plan')
    plan = read(local('edit_plan'))
    if len({c['id'] for c in plan['clips']}) != len(plan['clips']):
        raise ValueError('Repeated materials require a separate finishing profile')
    # Existing preflight verifies every media hash and the actual empty sequence.
    native_precheck(path, dry_run=True)
    from utils.premiere_project import load_premiere_project_root, find_project_sequence_node
    tree = load_premiere_project_root(local('project'))
    for name in names:
        if find_project_sequence_node(tree, name) is not None:
            raise ValueError('Checkpoint name already exists in source project: ' + name)
    effects, skipped = effect_plan(plan, cfg)
    project_sha = digest(local('project'))
    # Guard everything used to prepare this package, without changing workflow state.
    inputs = dict(review['artifacts'])
    inputs.update({str(p): digest(p) for p in (path, local('project'), local('edit_plan'), local('preset'))})
    summary = dict(status='VALIDATED_NO_ADOBE' if dry_run else 'PREPARED_NATIVE_NOT_RUN',
        duration_seconds=plan['duration_seconds'], clips=len(plan['clips']), photos=len(effects['motion']),
        transitions=len(effects['transitions']), color_clips=len(effects['color']), native_qa='PENDING',
        audio='Original clip audio; no loudness normalization or listening performed')
    if dry_run:
        print(json.dumps(summary, ensure_ascii=False)); return summary
    out = local('output_root') / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    prefix=cfg.get('output_prefix','Max26')
    if not prefix or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in prefix):
        raise ValueError('Invalid output_prefix')
    project = out / (prefix.upper()+'_FINISH_WORK.prproj')
    shutil.copyfile(local('project'), project)
    if digest(project) != project_sha:
        raise ValueError('Project copy hash mismatch')
    job = dict(project=project.as_posix(), sequence=cfg['target_sequence'], plan=plan,
               checkpoint_names=names, width=cfg['width'], height=cfg['height'],
               preset=local('preset').as_posix(), video=(out/(prefix+'_FINAL_'+('4K' if cfg['height']==2160 else '1080p')+'.mp4')).as_posix(), **effects)
    script = out / 'finish_all.jsx'; script.write_text(build_script(job), encoding='utf-8')
    write_json(out/'job.json', job)
    write_json(out/'inputs.json', inputs)
    write_json(out/'transition_plan.json', dict(selected=effects['transitions'], skipped=skipped))
    verify(inputs)
    summary.update(project=str(project), jsx=str(script), output=str(out), video=job['video'])
    write_json(out/'preparation_result.json', summary)
    (out/'START_RU.md').write_text(
        f'# {prefix}: один нативный запуск\n\n'
        f'1. Откройте рабочую копию: `{project}`.\n'
        f'2. В существующей панели Run Transition Script один раз запустите `{script}`.\n'
        f'3. Исполнитель создаст {" → ".join(names)} и экспортирует только последнюю последовательность.\n'
        f'4. Результат: `{job["video"]}`. Статус — finish_status.txt; журнал — finish_progress.log.\n\n'
        'Пакет подготовлен, но НЕ выполнялся в Adobe. Motion, QE transitions и Lumetri требуют нативной проверки; '
        'локализованные названия параметров могут отличаться. Скрипт остановится при несовпадении readback.\n\n'
        'Повтор на частично обработанной копии запрещён. При ошибке сохраните журнал, исправьте причину и '
        'подготовьте новый пакет из исходного проекта. Старые контрольные последовательности не удаляются. '
        'Автоматическое восстановление после сбоя не реализовано.\n\n'
        'Цвет: редактируемые Lumetri для индивидуальных поправок и общего профиля. '
        'Автоматический баланс белого, анализ кожи, звук и апскейл исходников не выполняются. '
        'Проверьте лица, поля кадра, движение, переход, цвет, речь и громкость в финальном MP4. '
        'Нативное открытие и экспорт ещё не подтверждены. Workflow не помечается автоматически принятым.\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(); prepare(args.config, args.dry_run)
