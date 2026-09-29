"""BM26 color preparation only; Adobe execution belongs to the user."""
import argparse,json,xml.etree.ElementTree as ET
from pathlib import Path
from prepare_full_master import ROOT,read,HELPERS
from ingest import digest,write_json
from classify import copy_verified,stamp
from utils import premiere_project as pp
from utils.premiere_sequence_motion import _track_item_contexts
from tools.prepare_plan_color import values

# Decisions from ordered native FINISH samples, not automatic luminance matching.
GROUPS=[
 (['FM002','FM003'],{'Exposure':.10,'Shadows':10},'Backlit living room: gently open faces; retain window/daylight contrast.'),
 (['FM014','FM023'],{'Temperature':-3,'Shadows':8},'Same warm costume episode: reduce yellow cast slightly without neutralizing room light.'),
 (['FM021'],{'Shadows':10},'Family table: lift shaded faces, preserve evening room.'),
 (['FM026'],{'Shadows':8,'Highlights':-10},'Drums versus piano: soften bright window and open instrument-side shadows.'),
 (['FM028'],{'Shadows':12,'Temperature':-3},'Older computer photo: gently open faces and reduce yellow; preserve softness.'),
 (['FM059','FM061'],{'Shadows':10},'Outdoor backlit portraits: open faces without raising sky exposure.'),
 (['FM084'],{'Exposure':.15,'Shadows':18},'Dark foreground family on sofa: modest lift, keep kitchen highlights.'),
 (['FM085'],{'Shadows':8},'Reading photo: small shadow lift only, preserve quiet mood.'),
 (['FM092','FM093'],{'Temperature':-3},'Adjacent floor-play photos: same slight warm-cast reduction.'),
 (['FM095'],{'Shadows':12},'Evening dinner: improve shaded faces; retain warm lamps.'),
 (['FM098','FM099'],{'Exposure':.15,'Shadows':12,'Temperature':-2},'One bedtime source: consistent mild face lift, preserve dusk and blue window.'),
 (['FM101','FM102','FM103'],{'Shadows':10},'One bedtime conversation source: consistent shadow opening, preserve night mood.'),
 (['FM104'],{'Temperature':-2},'Final family portrait: slightly reduce warmth before ART, no exposure/look boost.')]

def prepare(dry=False):
 task=ROOT/'tasks/BM26';work=task/'color';s=read(task/'visual_finish/status.json');qa=read(task/'visual_finish/verification.json')
 assert qa['status']=='TECHNICAL_PASS' and s['native_sequences_created'] and s['reviews_created']
 source=Path(s['project_checkpoint']);sha=digest(source);assert sha==qa['checkpoint_sha256']
 old=read(s['plan']);root=pp.load_premiere_project_root(source);ids=pp.build_project_object_id_lookup(root);uids=pp.build_project_object_uid_lookup(root)
 full={c['id']:c for c in old['versions'][0]['plan']['clips']};by_path={}
 for keys,settings,reason in GROUPS:
  values(settings)
  for key in keys:
   c=full[key];assert not c['art_type'];path=str(Path(c['path'])).lower()
   if path in by_path:assert by_path[path]['correction']==settings
   by_path[path]=dict(correction=settings,reason=reason,group=keys)
 versions=[];media={}
 for v in old['versions']:
  seq=pp.find_project_sequence_node(root,v['output_sequence']);assert seq is not None
  contexts=_track_item_contexts(seq,group_index=0,id_lookup=ids,uid_lookup=uids,project_path=source)
  assert len(contexts)==len(v['plan']['clips'])
  rows=[]
  for i,(a,c) in enumerate(zip(contexts,v['plan']['clips'])):
   assert str(Path(a.source_path)).lower()==str(Path(c['path'])).lower() and Path(c['path']).exists()
   offset=v.get('still_handle_offset_seconds',0) if i in v.get('still_handle_indices',[]) else 0
   for x,y in [(a.start,c['timeline_start_frame']/25),(a.end,(c['timeline_start_frame']+c['frames'])/25),(a.source_in,c['source_in_seconds']+offset),(a.source_out,c['source_out_seconds']+offset)]:assert abs(x/254016000000-y)<.021,(c['id'],x,y)
   spec=by_path.get(str(Path(c['path'])).lower(),{})
   if c['art_type']:assert not spec
   rows.append(dict(index=i,id=c['id'],path=c['path'],start_ticks=a.start,end_ticks=a.end,correction=spec.get('correction',{}),reason=spec.get('reason','ART palette/paper preserved.' if c['art_type'] else 'No clear technical correction needed in assessed native sample.'),group=spec.get('group',[]),art_type=c['art_type']))
   media.setdefault(c['path'],None)
  count=sum(bool(r['correction']) for r in rows)
  versions.append(dict(label=v['label'],source_sequence=v['output_sequence'],output_sequence=f"BM26_{v['label']}_COLOR_01",clips=rows,counts=dict(planned_corrected=count,planned_unchanged=len(rows)-count,planned_matched=sum(bool(r['correction']) and len(r['group'])>1 for r in rows),art_corrected=0,art_preserved=sum(bool(r['art_type']) for r in rows),bw_observed=0)))
 package=Path(read(task/'task.json')['classify']['permanent_project_dir'])/'COLOR'/'BM26_FINAL_COLOR_01'
 project=source.with_name('BAM_26_BM_1_FINAL_COLOR_01.prproj')
 assert not package.exists() and not project.exists()
 report=dict(status='DRY_RUN_PASS',source_sha256=sha,counts={v['label']:v['counts'] for v in versions},sequences=pp.list_named_project_sequence_names(root),media_paths_exist=len(media))
 if dry:print(json.dumps(report,ensure_ascii=True,indent=2));return
 for p in media:media[p]=digest(Path(p))
 work.mkdir(exist_ok=True);package.mkdir(parents=True);copy_verified(source,project)
 preset=ET.parse(old['preset']);changed=0
 for node in preset.getroot().iter():
  if node.findtext('ParamIdentifier')=='ADBEAudioBitrate':node.find('ParamValue').text='128';changed+=1
 assert changed==1
 preset.write(package/'review_720p25_1200k_aac128.epr',encoding='utf-8',xml_declaration=True)
 for v in versions:v['review']=(package/(v['output_sequence']+'_REVIEW.mp4')).as_posix()
 job=dict(project=project.as_posix(),source_checkpoint=source.as_posix(),source_sha256=sha,preset=(package/'review_720p25_1200k_aac128.epr').as_posix(),versions=versions,global_look={},look_decision='No additional look: retain natural episode lighting, skin, and ART palettes.',assessment='Ordered mid-shot samples of all 126 timeline instances reviewed; native corrected full-film review remains required.',media_sha256=media)
 template=(ROOT/'scripts/final_color_native.jsx').read_text(encoding='utf-8')
 helpers=HELPERS[:HELPERS.index('function checkpoint')]
 invariant=helpers[helpers.index('function signature'):helpers.index('function verifyCheckpoints')].replace('function signature(s)','function invariant(s)').replace('s.sequenceID,','').replace('var component=clip.components[co];parts.push(component.matchName);',"var component=clip.components[co];if(component.matchName==='AE.ADBE Lumetri')continue;parts.push(component.matchName);")
 script=template.replace('__HELPERS__',helpers+invariant).replace('__JOB__',json.dumps(job,ensure_ascii=True))
 (package/'apply_final_color.jsx').write_text(script,encoding='utf-8');(work/'native_syntax_check.js').write_text(script,encoding='utf-8')
 write_json(work/'color_plan.json',job);write_json(package/'color_plan.json',job);write_json(work/'dry_run.json',report)
 status=dict(task_id='BM26',stage='FINAL COLOR',status='FINAL COLOR PREPARED — NATIVE RUN PENDING',timestamp=stamp(),source_project=str(source),source_project_sha256=sha,project_checkpoint=str(project),jsx=str(package/'apply_final_color.jsx'),plan=str(package/'color_plan.json'),counts=report['counts'],actual_shots_corrected=0,reviews={v['label']:v['review'] for v in versions},reviews_created=False,previous_sequences_modified=False,source_media_modified=False,next='USER EXECUTES JSX → CODEX CHECKS → USER REVIEW',execution_state='STOP')
 write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
 state=read(task/'state.json');state.update(stage='FINAL COLOR',status=status['status'],color=status,next_stage=status['next']);state['stages']['FINAL_COLOR']='PREPARED_NATIVE_NOT_RUN';write_json(task/'state.json',state)
 assert digest(source)==sha==digest(project)
 print(json.dumps(status,ensure_ascii=True,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');a=p.parse_args();prepare(a.dry_run)
