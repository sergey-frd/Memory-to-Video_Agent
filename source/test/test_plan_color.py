import pytest
from tools.prepare_plan_color import global_values,values

def test_strength_interpolates_from_neutral():
    assert global_values(dict(profile='warm_family',strength=0))['Saturation']==100
    look=global_values(dict(profile='warm_family',strength=.35))
    assert look['Temperature']==pytest.approx(2.1)
    assert look['Saturation']==pytest.approx(101.4)

def test_explicit_overrides_and_bounds():
    assert values({'Exposure':.2})=={'Exposure':.2}
    for bad in [{'Exposure':float('nan')},{'Exposure':2},{'Unknown':0}]:
        with pytest.raises(ValueError):values(bad)
    with pytest.raises(ValueError):global_values(dict(profile='warm_family',strength=-1))
