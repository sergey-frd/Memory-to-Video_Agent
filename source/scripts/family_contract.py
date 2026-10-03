"""Small runtime contract shared by family orchestration and its adapters."""
import hashlib,json,math,re,os,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGES=['INGEST','CLASSIFY','ART_PLAN','ART','ART_CLASSIFY','BANK','WIDE_PLAN','FULL_PLAN','FULL_PREMIERE_HANDOFF','BRANCH_PLANS','BRANCH_PREMIERE_HANDOFF','STRUCTURE_REVIEW']
def read(p):
 for attempt in range(20):
  try:return json.loads(Path(p).read_text(encoding='utf-8-sig'))
  except PermissionError:
   if os.name!='nt' or attempt==19:raise
   time.sleep(.01*(attempt+1))
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def validate_schema(value,schema,where='$'):
 """Validate the deliberately limited JSON Schema vocabulary used in this repo."""
 typ=schema.get('type');types={'object':dict,'array':list,'string':str,'boolean':bool,'integer':int,'number':(int,float),'null':type(None)}
 if isinstance(typ,list):
  if value is None and 'null' in typ:return
  schema=dict(schema,type=next(t for t in typ if t!='null'));return validate_schema(value,schema,where)
 if typ and (not isinstance(value,types[typ]) or typ in ('integer','number') and isinstance(value,bool)):raise ValueError(where+': expected '+typ)
 if 'const' in schema and value!=schema['const']:raise ValueError(where+': invalid constant')
 if 'enum' in schema and value not in schema['enum']:raise ValueError(where+': invalid enum')
 if isinstance(value,(int,float)) and not isinstance(value,bool):
  if not math.isfinite(value) or value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf):raise ValueError(where+': number outside bounds')
 if isinstance(value,str):
  if len(value)<schema.get('minLength',0) or ('pattern' in schema and not re.search(schema['pattern'],value)):raise ValueError(where+': invalid string')
 if isinstance(value,list):
  if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',math.inf):raise ValueError(where+': invalid array length')
  for i,v in enumerate(value):validate_schema(v,schema.get('items',{}),where+f'[{i}]')
 if isinstance(value,dict):
  for k in schema.get('required',[]):
   if k not in value:raise ValueError(where+': missing '+k)
  for k,v in value.items():
   if k not in schema.get('properties',{}) and schema.get('additionalProperties') is False:raise ValueError(where+': unknown '+k)
   validate_schema(v,schema.get('properties',{}).get(k,{}),where+'.'+k)
def config(task,check_paths=True):
 cfg=read(task/'task.json');validate_schema(cfg,read(ROOT/'scripts/schemas/family_task.schema.json'))
 if cfg['task_id']!=task.name:raise ValueError('Task ID/directory mismatch')
 f=cfg['family_pipeline']
 for branch in ['main','short']:
  target=f[branch+'_target_seconds'];bounds=f[branch+'_target_range']
  if bounds is not None:
   lo,hi=bounds
   if target is not None and not 0<lo<=target<=hi:raise ValueError('Invalid '+branch+' duration range')
  elif branch=='short' and 'short_max_seconds' not in f:raise ValueError('SHORT needs range or hard maximum')
 if 'short_max_seconds' in f and f['short_target_seconds']>f['short_max_seconds']:raise ValueError('SHORT target exceeds maximum')
 for branch in ['main','short']:
  fmt=branch_format(cfg,branch);a,b=map(int,fmt['aspect_ratio'].split(':'))
  if fmt['width']*b!=fmt['height']*a:raise ValueError('Format aspect ratio mismatch')
  review=branch_review(cfg,branch)
  if review['width']*b!=review['height']*a:raise ValueError('Review aspect ratio mismatch')
 if len(set(cfg['heroes']))!=len(cfg['heroes']):raise ValueError('Duplicate heroes')
 if check_paths:
  for key in ['premiere_project','source_materials']:
   p=Path(cfg['paths'][key])
   if not p.is_absolute() or not p.exists():raise ValueError('Missing actual path: '+key+' '+str(p))
  p=Path(cfg['classify']['permanent_project_dir'])
  if not p.is_absolute() or not p.is_dir() or p.resolve().is_relative_to(ROOT):raise ValueError('Permanent storage must exist outside repository')
  for group in ['classify','classify_art']:
   rel=Path(cfg[group]['permanent_relative_dir'])
   if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe permanent relative directory')
 return cfg
def task_path(task_id):
 if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',task_id) or task_id.lower()=='legacy':raise ValueError('Invalid TASK ID')
 p=ROOT/'tasks'/task_id
 if p.resolve()!=p.absolute():
  # Hero data may live beside the source material; the repository holds only
  # a compatibility junction. Accept only the declared permanent task layout.
  cfg=read(p/'task.json')
  expected=Path(cfg['classify']['permanent_project_dir'])/'tasks'/task_id
  if cfg.get('task_id')!=task_id or p.resolve()!=expected.resolve():
   raise ValueError('Redirected task directory outside permanent task storage')
 return p
def artifacts(paths):return {str(Path(p).resolve()):sha(p) for p in paths}
def verify(record):
 for p,h in record.items():
  if not Path(p).is_file() or sha(p)!=h:raise ValueError('Checkpoint missing/changed: '+p)

def branch_format(cfg,branch):
 return cfg['family_pipeline'].get(branch.lower()+'_format',dict(width=3840,height=2160,fps=25,aspect_ratio='16:9'))
def branch_review(cfg,branch):
 return dict(dict(width=1280,height=720,fps=25),**cfg['family_pipeline'].get(branch.lower()+'_review',cfg['family_pipeline'].get('review',{})))
def missing_inputs(cfg):
 f=cfg['family_pipeline'];out=[]
 for k in ['main_target_seconds','main_target_range']:
  if k in f and f[k] is None:out.append(k)
 for k,v in f.get('art',{}).items():
  if v is None:out.append('art.'+k)
 return out
def duration_bounds(cfg,branch):
 f=cfg['family_pipeline'];bounds=f[branch.lower()+'_target_range']
 if branch=='SHORT' and 'short_max_seconds' in f:return .04,f['short_max_seconds']
 if bounds is None:raise ValueError('USER_INPUT_REQUIRED: '+branch+' duration')
 return bounds
def require_parent(source,branch):
 expected='WIDE' if branch=='FULL' else 'FULL'
 actual=source.get('branch')
 if actual is None:
  actual=next((b for b in ['WIDE','FULL','MAIN','SHORT'] if '_'+b+'_MASTER_' in source['sequence_name']),None)
 if actual!=expected:raise ValueError(branch+' must derive directly from '+expected)
