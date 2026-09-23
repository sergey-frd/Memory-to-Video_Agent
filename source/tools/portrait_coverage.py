"""Opt-in unbounded portrait policy and explicit facet coverage audit."""
import copy
import re


def duration_options(cfg):
    mode=cfg.get('duration_mode','compact')
    if mode=='coverage':
        if cfg.get('max_duration_seconds') is not None or cfg.get('target_duration_seconds') is not None:
            raise ValueError('Coverage mode cannot specify a runtime target or cap')
        return dict(duration_mode='coverage',target_duration_seconds=None)
    if mode!='compact' or type(cfg.get('max_duration_seconds')) is not int or cfg['max_duration_seconds']<=0:
        raise ValueError('Compact mode requires a positive duration cap')
    return dict(duration_mode='compact',target_duration_seconds=cfg['max_duration_seconds'],max_duration_seconds=cfg['max_duration_seconds'])


def neutral_catalog(rows):
    result=copy.deepcopy(rows)
    for row in result:
        row['description']=re.sub(r'; пригодность [^,;]+, не обязательное включение\.', '.',row['description'])
    return result


def audit_schema(schema,facets):
    schema=copy.deepcopy(schema)
    schema['required'].append('coverage_audit')
    schema['properties']['coverage_audit']=dict(type='array',items=dict(type='object',additionalProperties=False,
        required=['facet_id','selected_ids','reason','gap'],properties=dict(
            facet_id=dict(type='string',enum=[f['id'] for f in facets]),
            selected_ids=dict(type='array',items=dict(type='string')),
            reason=dict(type='string'),gap=dict(type='string'))))
    return schema


def validate_audit(plan,facets):
    items=plan.get('coverage_audit',[])
    expected={f['id'] for f in facets}
    if len(items)!=len(expected) or {x['facet_id'] for x in items}!=expected:
        raise ValueError('Coverage audit must address every facet exactly once')
    selected={i['material_id'] for b in plan['blocks'] for i in b['items']}
    for item in items:
        if not set(item['selected_ids']).issubset(selected):raise ValueError('Coverage audit references unselected material')
        if not item['reason'].strip():raise ValueError('Coverage reasoning required')
        if not item['selected_ids'] and not item['gap'].strip():raise ValueError('Omitted facet requires explicit gap')


COVERAGE_PROMPT='''Task-specific coverage policy overrides general compact-film runtime guidance.
There is NO minimum, maximum, target runtime or desired shot count. Do not aim for
3-5 minutes or a longer duration than another version. Use the entire catalog as
the candidate pool. Completeness means distinct supported facets and situations;
do not include redundant shots or stretch stills. Original photographs, videos,
watercolors and double exposures compete equally on evidence and narrative value.
Historical preferred/suitable ART ratings describe assessments, NOT selection
priority. Preserve source-to-ART relationships; justify any paired use. No type
quota and no requirement to use all materials. Address each supplied coverage
facet in coverage_audit, with selected IDs, specific reason and remaining gap.
Distinguish visible evidence, biographical context, metaphor and unknown identities.
Do not claim a metaphor or generic group shot proves a school, lesson, migration,
named relative or childhood event. A supported gap is better than invented footage.
For each shot, give its new contribution and why its reading time is sufficient.
'''
