"""Clone verified FULL COLOR graph and retain an explicit editorial selection.

All media/effect objects of previous sequences remain unchanged. Native opening,
effect readback and review export still require the user's Premiere run.
"""
import argparse,copy,gzip,json,shutil,xml.etree.ElementTree as ET
from pathlib import Path
from prepare_full_master import ROOT,read,HELPERS
from ingest import digest,write_json
from classify import stamp,copy_verified
from utils import premiere_project as pp
from utils.premiere_project_export import clone_named_sequence,_update_sequence_duration_metadata
from utils.premiere_sequence_motion import _track_item_contexts
T=254016000000

def set_text(n,k,v):
 e=n.find(k)
 if e is None:e=ET.SubElement(n,k)
 e.text=str(v)

def contexts(root,seq,project,g):
 return _track_item_contexts(seq,group_index=g,id_lookup=pp.build_project_object_id_lookup(root),uid_lookup=pp.build_project_object_uid_lookup(root),project_path=project)

def effect_graph(item,ids):
 """Canonical component graph without object numbers (clone IDs differ)."""
 ref=item.find('./ClipTrackItem/ComponentOwner/Components');seen={}
 def walk(n):
  attrs={k:v for k,v in n.attrib.items() if k not in ['ObjectID','ObjectUID','ObjectRef','ObjectURef']}
  children=[walk(e) for e in n]
  target=n.get('ObjectRef')
  if target:
   if target in seen:children.append(['REF',seen[target]])
   else:seen[target]=len(seen);children.append(walk(ids[target]))
  return [n.tag,attrs,(n.text or '').strip(),children]
 return walk(ref)

def prepare(dry=False):
 task=ROOT/'tasks/BM26';work=task/'main';cs=read(task/'color/status.json');cj=read(cs['plan']);source=Path(cs['project_checkpoint']);source_hash=digest(source)
 assert (Path(cs['jsx']).parent/'native_status.txt').read_text().strip()=='EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA'
 root=pp.load_premiere_project_root(source);source_seq=pp.find_project_sequence_node(root,'BM26_FULL_COLOR_01');assert source_seq is not None
 ids=pp.build_project_object_id_lookup(root);uids=pp.build_project_object_uid_lookup(root)
 old_nodes={id(n):ET.tostring(n) for n in root if n.tag!='RootProjectItem'}
 full=read(read(task/'full_master/status.json')['plan']);fc={c['id']:c for c in full['clips']};source_rows=contexts(root,source_seq,source,0)
 assert len(source_rows)==106 and round(max(a.end for a in source_rows)/T,2)==1067.56
 selections=read(work/'editorial_selection.json');shots=[];cursor=0;used=set()
 for key,lo,hi,chapter,reason in selections['shots']:
  c=fc[key];i=int(key[2:])-1;a=source_rows[i];assert key not in used;used.add(key)
  assert str(Path(a.source_path)).lower()==str(Path(c['path'])).lower() and Path(c['path']).exists()
  assert c['speed']==1,'MAIN deliberately keeps normal-speed segments only'
  if lo is None:head=0;duration=(a.end-a.start)/T
  else:
   assert c['kind']=='video' and c['source_in_seconds']<=lo<hi<=c['source_out_seconds']+1e-6
   head=lo-c['source_in_seconds'];duration=hi-lo
  frames=round(duration*25);assert abs(frames/25-duration)<1e-6
  row=dict(id=f'MN{len(shots)+1:03d}',source_full_id=key,source_index=i,path=a.source_path,kind=c['kind'],art_type=c['art_type'],chapter=chapter,reason=reason,
   source_full_start_ticks=a.start+round(head*T),source_full_end_ticks=a.start+round((head+duration)*T),start_ticks=cursor*T//25,end_ticks=(cursor+frames)*T//25,
   source_in_ticks=a.source_in+round(head*T),source_out_ticks=a.source_in+round((head+duration)*T),speed=1,frames=frames)
  shots.append(row);cursor+=frames
 assert 300<=cursor/25<=360
 # Clone complete object graph. No original track/effect objects are reused.
 main=clone_named_sequence(root,source_sequence_name='BM26_FULL_COLOR_01',new_sequence_name='BM26_MAIN_01',object_id_lookup=ids,object_uid_lookup=uids)
 ids=pp.build_project_object_id_lookup(root);uids=pp.build_project_object_uid_lookup(root)
 expected_audio=[]
 for g in [0,1]:
  for ti,tr in pp.get_project_track_nodes(main,track_group_index=g,object_id_lookup=ids,object_uid_lookup=uids):
   container=tr.find('./ClipTrack/ClipItems/TrackItems')
   if container is None:continue
   retained=[]
   for ref in list(container):
    item=ids[ref.get('ObjectRef')];st,en=pp.resolve_project_track_item_timeline(item);matches=[s for s in shots if st<=s['source_full_start_ticks'] and en>=s['source_full_end_ticks']]
    assert len(matches)<=1,'One input clip cannot service two MAIN fragments in this executor'
    if not matches:continue
    s=matches[0];clip=pp.resolve_project_track_item_clip(item,ids);oldin,oldout=pp.resolve_project_track_item_source_bounds(item,ids)
    head=s['source_full_start_ticks']-st;dur=s['end_ticks']-s['start_ticks'];assert abs((oldout-oldin)-(en-st))<=T/50
    payload=clip.find('Clip');set_text(payload,'InPoint',oldin+head);set_text(payload,'OutPoint',oldin+head+dur)
    timeline=item.find('./ClipTrackItem/TrackItem');set_text(timeline,'Start',s['start_ticks']);set_text(timeline,'End',s['end_ticks'])
    retained.append((s['start_ticks'],ref))
    if g==1:expected_audio.append(dict(track=ti,source_full_id=s['source_full_id'],path=pp.resolve_project_track_item_source_path(item,ids,uids,project_path=source),start_ticks=s['start_ticks'],end_ticks=s['end_ticks'],source_in_ticks=oldin+head,source_out_ticks=oldin+head+dur))
   for ref in list(container):container.remove(ref)
   for n,(_,ref) in enumerate(sorted(retained,key=lambda x:x[0])):ref.set('Index',str(n));container.append(ref)
 transitions=[]
 for ti,tr in pp.get_project_track_nodes(main,track_group_index=0,object_id_lookup=ids,object_uid_lookup=uids):
  container=tr.find('./ClipTrack/TransitionItems/TrackItems')
  if container is None:continue
  for ref in list(container):
   node=ids[ref.get('ObjectRef')];tl=node.find('./TransitionTrackItem/TrackItem');st=int(tl.findtext('Start'));en=int(tl.findtext('End'));cut=(st+en)//2
   left=next((s for s in shots if s['source_full_end_ticks']==cut),None);right=next((s for s in shots if s['source_full_start_ticks']==cut),None)
   assert left and right and left['end_ticks']==right['start_ticks'],'Selected edit must preserve both sides of existing dissolves'
   delta=right['start_ticks']-cut;set_text(tl,'Start',st+delta);set_text(tl,'End',en+delta)
   transitions.append(dict(track=ti,start_ticks=st+delta,end_ticks=en+delta,left=left['source_full_id'],right=right['source_full_id']))
 _update_sequence_duration_metadata(root,main,new_total_duration=cursor*T//25)
 # Every pre-existing object except root project-item listing remains byte-identical.
 for n in root:
  if id(n) in old_nodes:assert ET.tostring(n)==old_nodes[id(n)],'Original object changed '+n.tag
 actual=contexts(root,main,source,0);assert len(actual)==len(shots)
 for a,s in zip(actual,shots):
  assert (a.start,a.end,a.source_in,a.source_out)==tuple(s[k] for k in ['start_ticks','end_ticks','source_in_ticks','source_out_ticks'])
  assert effect_graph(a.track_item_node,ids)==effect_graph(source_rows[s['source_index']].track_item_node,ids),'Effect graph changed'
 audio=contexts(root,main,source,1);assert len(audio)==len(expected_audio)
 for a,e in zip(sorted(audio,key=lambda a:(a.track_index,a.start)),sorted(expected_audio,key=lambda e:(e['track'],e['start_ticks']))):assert (a.track_index,a.start,a.end,a.source_in,a.source_out)==(e['track'],e['start_ticks'],e['end_ticks'],e['source_in_ticks'],e['source_out_ticks'])
 removed=[dict(id=c['id'],reason='Повторный художественный акцент.' if c['art_type'] else 'Повтор близкого смысла/ожидание/менее выразительный общий план; выбран более сильный момент того же тематического блока.') for c in full['clips'] if c['id'] not in used]
 package=Path(read(task/'task.json')['classify']['permanent_project_dir'])/'MAIN'/'BM26_MAIN_01';project=source.with_name('BAM_26_BM_1_MAIN_01.prproj')
 assert not package.exists() and not project.exists()
 report=dict(status='DRY_RUN_PASS',source_hash=source_hash,source_sequence='BM26_FULL_COLOR_01',source_duration=1067.56,main_duration=cursor/25,segments_reviewed=106,visual_samples=208,segments_used=len(shots),segments_removed=len(removed),audio_items=len(audio),transitions=len(transitions),previous_objects_unchanged=True,effects_inherited=True,full_audiovisual_review='PENDING_NATIVE_EXPORT')
 if dry:print(json.dumps(report,indent=2));return
 job=dict(project=project.as_posix(),source_project=source.as_posix(),source_sha256=source_hash,sequence='BM26_MAIN_01',source_sequence='BM26_FULL_COLOR_01',duration_seconds=cursor/25,clips=shots,audio=expected_audio,transitions=transitions,removed=removed,story=selections['story'],preset=cj['preset'],video=(package/'BM26_MAIN_01_REVIEW.mp4').as_posix(),no_music=True,no_global_recolor=True)
 write_json(work/'main_plan.json',job);write_json(work/'dry_run.json',report)
 package.mkdir(parents=True);project.write_bytes(gzip.compress(ET.tostring(root,encoding='utf-8',xml_declaration=True)))
 # Re-read serialized project, not just the in-memory clone.
 check=pp.load_premiere_project_root(project);checkseq=pp.find_project_sequence_node(check,'BM26_MAIN_01');assert len(contexts(check,checkseq,project,0))==len(shots)
 lines=['# BM26 MAIN — Братья','',selections['story'],'',f'Первый монтаж: {cursor/25:.2f} с. 54 фрагмента из FULL COLOR; SHORT не использован как основа.','', 'Границы речи/музыкальных фраз и непрерывный просмотр со звуком требуют нативного review. Пока выполнена визуальная редакторская оценка 208 выборок всего FULL, а не полный аудиовизуальный QA.','']
 for s in shots:lines.append(f"{s['id']} | {s['start_ticks']/T:.2f}–{s['end_ticks']/T:.2f} | {s['chapter']} | {s['source_full_id']} | {s['reason']}")
 (work/'MAIN_SCRIPT_RU.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 for name in ['main_plan.json','editorial_selection.json','MAIN_SCRIPT_RU.md','dry_run.json','request.txt']:copy_verified(work/name,package/name)
 native=(ROOT/'scripts/main_cut_native.jsx').read_text(encoding='utf-8').replace('__HELPERS__',HELPERS[:HELPERS.index('function checkpoint')]).replace('__JOB__',json.dumps(job,ensure_ascii=True))
 (package/'check_export_main.jsx').write_text(native,encoding='utf-8');(work/'native_syntax_check.js').write_text(native,encoding='utf-8')
 assert digest(source)==source_hash
 status=dict(task_id='BM26',stage='MAIN CUT',status='MAIN PREPARED — NATIVE OPEN CHECK / REVIEW PENDING',timestamp=stamp(),source_project=str(source),source_project_sha256=source_hash,project_checkpoint=str(project),project_prepared_sha256=digest(project),jsx=str(package/'check_export_main.jsx'),plan=str(package/'main_plan.json'),review=job['video'],counts=report,native_verified=False,review_created=False,execution_state='STOP',next='USER RUNS NATIVE CHECK / EXPORT → CODEX CHECKS → USER REVIEW')
 write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
 cs.update(status='FINAL COLOR COMPLETED — USER CONFIRMED',user_confirmation='завершен COLOR',native_sequences_created=True,reviews_created=True,completion_basis='Explicit user confirmation; native log and saved COLOR sequences observed. Not a claim of agent full audiovisual QA.')
 write_json(task/'color/status.json',cs)
 state=read(task/'state.json');state.update(stage='MAIN CUT',status=status['status'],main=status,color=cs,next_stage=status['next']);state['stages']['FINAL_COLOR']='COMPLETED_USER_CONFIRMED';state['stages']['MAIN_CUT']='PREPARED_NATIVE_QA_PENDING';write_json(task/'state.json',state)
 print(json.dumps(status,ensure_ascii=True,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');a=p.parse_args();prepare(a.dry_run)
