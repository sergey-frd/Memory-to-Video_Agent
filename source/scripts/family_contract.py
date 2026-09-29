"""Small runtime contract shared by family orchestration and its adapters."""
import hashlib,json,math,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGES=['INGEST','CLASSIFY','ART_PLAN','ART','ART_CLASSIFY','BANK','WIDE_PLAN','FULL_PLAN','FULL_PREMIERE_HANDOFF','BRANCH_PLANS','BRANCH_PREMIERE_HANDOFF','STRUCTURE_REVIEW']
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def validate_schema(value,schema,where='$'):
 """Validate the deliberately limited JSON Schema vocabulary used in this repo."""
 typ=schema.get('type');types={'object':dict,'array':list,'string':str,'boolean':bool,'integer':int,'number':(int,float),'null':type(None)}
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
  target=f[branch+'_target_seconds'];lo,hi=f[branch+'_target_range']
  if not 0<lo<=target<=hi:raise ValueError('Invalid '+branch+' duration range')
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
 if p.resolve()!=p.absolute():raise ValueError('Redirected task directory')
 return p
def artifacts(paths):return {str(Path(p).resolve()):sha(p) for p in paths}
def verify(record):
 for p,h in record.items():
  if not Path(p).is_file() or sha(p)!=h:raise ValueError('Checkpoint missing/changed: '+p)
