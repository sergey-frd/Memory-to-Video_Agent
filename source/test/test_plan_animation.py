import pytest
from tools.prepare_plan_animation import motion_keys

def test_profile_reverses_without_overshoot():
    keys=motion_keys(175,.58,.14,.18)
    values=[v for _,v in keys]
    assert len(keys)==175 and keys[-1][0]==174
    assert values[0]==1
    assert max(values)==pytest.approx(1.14)
    assert values[-1]==pytest.approx(1+.14*.82)
    peak=values.index(max(values))
    assert all(a<=b for a,b in zip(values[:peak],values[1:peak+1]))
    assert all(a>=b for a,b in zip(values[peak:],values[peak+1:]))

@pytest.mark.parametrize('args',[(2,.58,.14,.18),(75,1,.14,.18),(75,.58,1,.18)])
def test_invalid_profile(args):
    with pytest.raises(ValueError):motion_keys(*args)
