"""Prepare native branch scaffolds and a separate post-edit verifier; never launches Adobe."""
import copy,json
from pathlib import Path
from family_contract import read,sha,verify,artifacts,branch_review
from ingest import write_json
from classify import copy_verified
from scripts.prepare_full_master import make_xml,HELPERS

def prepare(task,cfg,source,plans):
 from family_stages import metadata,ROOT,T
 import xml.etree.ElementTree as ET
 from utils import premiere_project as pp
 from utils.premiere_sequence_motion import _track_item_contexts
 root=pp.load_premiere_project_root(source);ids=pp.build_project_object_id_lookup(root);uids=pp.build_project_object_uid_lookup(root)
 for plan in plans:
  seq=pp.find_project_sequence_node(root,plan['sequence_name'])
  if plan['branch']=='MAIN':
   if seq is None:raise ValueError('Existing MAIN target is absent')
   for group in [0,1]:
    if _track_item_contexts(seq,group_index=group,id_lookup=ids,uid_lookup=uids,project_path=source):raise ValueError('MAIN target must be empty before manual paste')
  elif seq is not None:raise ValueError('SHORT target already exists; preserve it')
 package=Path(cfg['classify']['permanent_project_dir'])/'pipeline'/task.name/'BRANCH_MANUAL_01'
 project=Path(cfg['classify']['permanent_project_dir'])/'projects'/(task.name+'_BRANCHES_CHECKPOINT_01.prproj')
 if package.exists() or project.exists():raise ValueError('Branch work package exists; inspect partial preparation before retry')
 package.mkdir(parents=True);copy_verified(source,project)
 jobs=[];scripts=[];files=[];aliases={}
 template=(ROOT/'scripts/full_master_native.jsx').read_text(encoding='utf-8')
 for original in plans:
  plan=copy.deepcopy(original);review=branch_review(cfg,plan['branch'])
  for c in plan['clips']:
   path=Path(c['path']);c.update(metadata(path));c['fit_scale']=100*min(plan['width']/c['width'],plan['height']/c['height']);c['audio']='source' if c['kind']=='video' and c['speed']==1 and c['channels'] else 'muted'
   if c.get('art_type'):
    alias=package/'media'/(task.name+'_BRANCH_'+sha(path)[:16]+'_'+path.name)
    if not alias.exists():copy_verified(path,alias)
    c['path']=str(alias);aliases[str(alias)]=sha(alias)
  final_name=plan['sequence_name'];assembly_name=task.name+'_MAIN_ASSEMBLY_01' if plan['branch']=='MAIN' else final_name
  assembly=copy.deepcopy(plan);assembly['sequence_name']=assembly_name
  xml=package/(assembly_name+'.xml');xml.write_text(make_xml(assembly,assembly),encoding='utf-8');files.append(xml)
  preset=ET.parse(ROOT/'scripts/presets/review_720p25.epr')
  values={'ADBEVideoWidth':review['width'],'ADBEVideoHeight':review['height'],'ADBEVideoFPS':T//25,'ADBEVideoMatchSource':'false','ADBEVideoTargetBitrate':review['video_mbps'],'ADBEVideoMaxBitrate':1.5,'ADBEAudioBitrate':review['audio_kbps']}
  for n in preset.getroot().iter('ExporterParam'):
   if n.findtext('ParamIdentifier') in values:n.find('ParamValue').text=str(values[n.findtext('ParamIdentifier')])
  preset_path=package/(final_name+'_review.epr');preset.write(preset_path,encoding='utf-8',xml_declaration=True);files.append(preset_path)
  job=dict(project=project.as_posix(),sequence=final_name,source_sequence=plan['source_sequence'],plan=plan,xml=xml.as_posix(),review=review,preset=preset_path.as_posix(),video=(package/(final_name+'_REVIEW.mp4')).as_posix())
  assembly_job=dict(job,sequence=assembly_name,plan=assembly)
  js=template.replace('__HELPERS__',HELPERS[:HELPERS.index('function cloneNamed')]).replace('__JOB__',json.dumps(assembly_job,ensure_ascii=True))
  start=js.index(" state('NATIVE_TIMELINE_CHECKED_EXPORTING')");end=js.index('}catch(e)',start)
  js=js[:start]+" state('ASSEMBLY_READY_MANUAL_EDIT_REQUIRED');\n"+js[end:]
  scripts.append(js);jobs.append(job)
 assembly_js=package/'assemble_branches.jsx';assembly_js.write_text('\n'.join(scripts)+"\nalert('Assembly ready. Copy MAIN_ASSEMBLY into the empty MAIN target, reframe SHORT manually, save, then run verify_and_export.jsx.');\n",encoding='utf-8');files.append(assembly_js)
 # Reuse the exact native timing/source readback and export code, with NO conform
 # or scale writes after the user has adjusted editable Motion.
 checks=[]
 for index,job in enumerate(jobs):
  js=template.replace('__HELPERS__',HELPERS[:HELPERS.index('function cloneNamed')]).replace('__JOB__',json.dumps(job,ensure_ascii=True))
  start=js.index('try{');end=js.index(" exact(seq.end",start)
  checks_body="""try{
 if(!app.project||!same(app.project.path,job.project))throw Error('Open branch work project');
 var seq=sequence(job.sequence);if(!seq)throw Error('Target sequence missing');
 if(seq.frameSizeHorizontal!==job.plan.width||seq.frameSizeVertical!==job.plan.height)throw Error('Wrong target dimensions');
 exact(seq.timebase,frame,'timebase');
 if(seq.videoTracks[0].clips.numItems!==job.plan.clips.length)throw Error('Video clip count');
 var i,j;
 for(i=1;i<seq.videoTracks.numTracks;i++)if(seq.videoTracks[i].clips.numItems)throw Error('Unexpected video track');
 for(i=0;i<job.plan.clips.length;i++)check(seq.videoTracks[0].clips[i],job.plan.clips[i]);
 var audioRows=[];for(i=0;i<job.plan.clips.length;i++)if(job.plan.clips[i].audio==='source')audioRows.push(job.plan.clips[i]);
 if(seq.audioTracks.numTracks<2)throw Error('Stereo tracks absent');
 for(i=0;i<seq.audioTracks.numTracks;i++){
  var expected=i<2?audioRows:[];
  if(seq.audioTracks[i].clips.numItems!==expected.length)throw Error('Audio clip count');
  for(j=0;j<expected.length;j++)check(seq.audioTracks[i].clips[j],expected[j]);
 }
 if(new File(job.video).exists)throw Error('Review exists: inspect before rerunning');
"""
  js=js[:start]+checks_body+js[end:]
  js=js.replace("alert('FULL review created. Send result for verification: '+job.video);",'')
  if index==0:js=js.replace("state('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA')","state('CHECKPOINT_EXPORTED_MORE_PENDING')")
  checks.append(js)
 verifier=package/'verify_and_export.jsx';verifier.write_text('\n'.join(checks)+"\nalert('Checkpoints exported. Run the resume command.');\n",encoding='utf-8');files.append(verifier)
 instructions=package/'USER_ACTION_RU.md'
 instructions.write_text('''# Katya26: ручная сборка и композиция

1. Откройте рабочую копию проекта из handoff.json. Запустите assemble_branches.jsx через Run Transition Script один раз.
2. В Katya26_MAIN_ASSEMBLY_01 выделите все клипы видео и аудио и скопируйте. В существующей пустой KatyaZ_26_v01 поставьте курсор в 00:00:00:00, выберите V1/A1 и вставьте. Не создавайте вложенную sequence. Исходную ASSEMBLY сохраните.
3. В Katya26_SHORT_MASTER_01 вручную выполните композицию каждого из 16 кадров: Motion → uniform Scale / Position и при необходимости ключевые кадры. Заготовка использует proportional contain, а не crop: рамки допустимы только до ручной отделки. Сохраняйте лица, руки, взаимодействие; не растягивайте ширину отдельно от высоты. Видео просмотрите целиком. Не меняйте длительности/порядок на этом шаге: если нужен другой материал, сообщите для пересмотра плана.
4. Сохраните рабочий проект. Запустите verify_and_export.jsx один раз: он не перезаписывает Motion, проверяет источники/тайминг и экспортирует MAIN 1280x720 и SHORT 720x1280.
5. После Checkpoints exported: scripts\\run_all.bat Katya26 --resume. При ошибке не повторяйте JSX, пришлите журнал.

Native QA и качество композиции до выполнения этих действий НЕ подтверждены.
''',encoding='utf-8');files.append(instructions)
 h=dict(status='USER_ACTION_REQUIRED',task=task.name,stage='BRANCH_PREMIERE_HANDOFF',project=str(project),source_project=str(source),source_sha256=sha(source),jsx=str(verifier),assembly_jsx=str(assembly_js),jobs=jobs,aliases=aliases,expected_result=[j['sequence'] for j in jobs],user_action=str(instructions),manual_reframing_required=True,resume_command=f'"{ROOT / "scripts/run_all.bat"}" {task.name} --resume',monitor_command=f'"{ROOT / "scripts/run_all.bat"}" {task.name} --watch')
 h['prepared_artifacts']=artifacts([*files,*aliases]);verify(h['prepared_artifacts'])
 out=task/'pipeline/BRANCH_PREMIERE_HANDOFF/handoff.json';write_json(out,h);write_json(package/'handoff.json',h)
 return h
