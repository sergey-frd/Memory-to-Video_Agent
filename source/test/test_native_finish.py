import json
from pathlib import Path
import shutil
import subprocess

import pytest
from tools import prepare_native_finish as mod


def test_stage_template_is_fail_closed(monkeypatch):
    monkeypatch.setattr(mod, 'JSX', mod.JSX.replace(" step('activate sequence');", " step('changed');"))
    with pytest.raises(ValueError, match='template changed'):
        mod.build_script({})


@pytest.mark.parametrize('names', [['A', 'B', 'C'], ['A', 'B', 'C', 'D']])
def test_generated_executor_parses_and_has_one_export(names):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node syntax checker unavailable')
    script = mod.build_script(dict(plan={}, checkpoint_names=names))
    subprocess.run([node, '--check'], input=script, text=True, capture_output=True, check=True)
    assert script.count('exportAsMediaDirect(') == 1
    assert script.index('applyColor();checkpoint();') < script.index('exportAsMediaDirect(')
    if len(names) == 3:
        assert 'job.checkpoint_names[3]' not in script


def test_clone_tracks_identity_and_checkpoint_mutation():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    # Execute the real controller helpers against a minimal host; no Adobe mock
    # effect execution is presented as native validation.
    harness = r'''
const assert=require('assert');
var app={project:{sequences:[],openSequence:function(){return true;}}};
function sequence(id){return {sequenceID:id,frameSizeHorizontal:1920,frameSizeVertical:1080,
 timebase:1,end:10,videoTracks:{numTracks:0},audioTracks:{numTracks:0}};}
var original=sequence('source');
app.project.sequences.push(original);app.project.sequences.numSequences=1;
original.clone=function(){app.project.sequences.push(sequence('clone'));app.project.sequences.numSequences=2;return true;};
var result=cloneNamed(original,'MOTION');
assert.strictEqual(result.sequenceID,'clone');assert.strictEqual(result.name,'MOTION');
assert.strictEqual(original.name,undefined);
checkpoints.push({sequence:original,signature:signature(original)});
verifyCheckpoints();original.end=20;
assert.throws(verifyCheckpoints,/Previous checkpoint changed/);
original.clone=function(){return false;};
assert.throws(function(){cloneNamed(original,'BAD');},/clone failed/);
'''
    subprocess.run([node, '-e', mod.HELPERS + harness], capture_output=True, text=True, check=True)


def test_requested_transition_must_have_source_handles():
    # Use the actual prepared source-based plan, if this local task is present.
    cfg_path = Path('config_native_finish_Max26.local.json')
    if not cfg_path.exists():
        pytest.skip('Local Max26 fixture not present')
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    plan = json.loads(Path(cfg['edit_plan']).read_text(encoding='utf-8'))
    effects, _ = mod.effect_plan(plan, cfg)
    assert len(effects['motion']) == 1
    assert [t['cut_frame'] for t in effects['transitions']] == [2100]
    # At the beginning of the reading source, no incoming head handle exists.
    cfg['transitions']['cut_frames'] = [2300]
    with pytest.raises(ValueError, match='lack verified handles'):
        mod.effect_plan(plan, cfg)


def test_checkpoint_uses_disk_save_evidence_not_return_type():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    harness = r'''
const assert=require('assert');
var seq={name:'ASSEMBLY'},job={project:'copy.prproj'},answer,fresh=true;
var app={project:{save:function(){return answer;}}};
function validateTimeline(){} function step(){} function log(){} function state(){}
signature=function(){return 'unchanged';};verifyCheckpoints=function(){};
function File(){this.exists=true;this.length=100;this.modified=new Date(fresh?Date.now():0);}
for(var i=0;i<4;i++){
 answer=[0,true,undefined,'0'][i];checkpoint();
}
assert.strictEqual(checkpoints.length,4);
fresh=false;answer=0;
assert.throws(checkpoint,/not confirmed on disk/);
'''
    subprocess.run([node, '-e', mod.HELPERS+harness], capture_output=True, text=True, check=True)
