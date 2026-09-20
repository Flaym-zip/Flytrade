from dataclasses import replace
import json

import numpy as np
import pytest

from app.brain import MushroomBody
from app.compartment_brain import CircuitSettings, CompartmentBrain, N_KC
from app.engine import Lab, Scenario


def scenario(**kw):
    args = dict(history_returns=[.12,.10,.08,.14,.10,.06],
                future_returns=[.08,.06,.10,.12,.08,.10], epsilon=0)
    args.update(kw)
    return Scenario(**args)


def test_sizes_and_rate_bounds():
    b=CompartmentBrain(); d=b.decide(scenario().observation(),0)
    assert len(d.kc)==1024 and len(d.extra['pn'])==108
    assert b.w_plus.shape==(3,1024,3)
    assert 0 < np.count_nonzero(d.kc) < N_KC//2
    assert np.all((d.kc>=0)&(d.kc<=1))
    assert len(d.extra['settling'])==25


def test_apl_really_inhibits_activity():
    b=CompartmentBrain(); obs=scenario().observation()
    on,_,view=b.encode(obs)
    off,_,offview=b.encode(obs,CircuitSettings(apl=False))
    assert on.mean()<off.mean()
    assert np.count_nonzero(on)<np.count_nonzero(off)
    assert view['apl']>0 and offview['apl']==0
    assert np.max(np.abs(np.array([t['apl'] for t in view['settling'][-5:]])-view['apl']))<1e-4


def test_no_future_no_title_no_price_scale_leakage():
    sa=scenario(name='up',future_returns=[.2]*6)
    sb=scenario(name='down',price=2000,future_returns=[-.2]*6)
    a,b=CompartmentBrain(1),CompartmentBrain(1)
    da,db=a.decide(sa.observation(),0),b.decide(sb.observation(),0)
    assert da.action==db.action
    np.testing.assert_allclose(da.features,db.features,atol=1e-12)
    np.testing.assert_allclose(da.kc,db.kc,atol=1e-12)
    np.testing.assert_array_equal(da.scores,db.scores)


def test_identical_observation_identical_neural_state_despite_hidden_reversal():
    a,b=CompartmentBrain(91),CompartmentBrain(91)
    sa=scenario(); sb=scenario(future_returns=[-x for x in sa.future_returns])
    da,db=a.decide(sa.observation(),0),b.decide(sb.observation(),0)
    np.testing.assert_array_equal(da.kc,db.kc)
    assert da.extra==db.extra and da.action==db.action


def test_filters_only_use_observed_history_and_can_be_ablated():
    b=CompartmentBrain(); obs=scenario().observation()
    f=b.features(obs,True); g=b.features(obs,False)
    np.testing.assert_array_equal(f[:8],g[:8])
    assert np.any(f[8:]!=0) and np.all(g[8:]==0)
    assert len(f)==12


@pytest.mark.parametrize('reward',[-1.,1.])
def test_update_only_chosen_action_and_active_kc_when_forgetting_off(reward):
    b=CompartmentBrain();d=b.decide(scenario().observation(),0)
    wp=b.w_plus.copy();wm=b.w_minus.copy()
    result=b.reinforce(d,reward,.25)
    other=[x for x in range(3) if x!=d.action]
    np.testing.assert_array_equal(b.w_plus[:,:,other],wp[:,:,other])
    np.testing.assert_array_equal(b.w_minus[:,:,other],wm[:,:,other])
    np.testing.assert_array_equal(b.w_plus[:,d.kc==0],wp[:,d.kc==0])
    assert np.sign(result['post_scores'][d.action])==np.sign(reward)
    changes=[c['weight_change'] for c in result['compartments']]
    assert changes[0]>changes[1]>changes[2]>0


def test_single_compartment_freezes_other_two():
    b=CompartmentBrain();d=b.decide(scenario().observation(),0,CircuitSettings(compartments=False))
    old=b.w_plus.copy();r=b.reinforce(d,1,.25)
    np.testing.assert_array_equal(b.w_plus[1:],old[1:])
    assert r['compartments'][0]['active'] and not r['compartments'][1]['active']
    assert r['post_scores']==r['compartments'][0]['scores']


def test_feedback_decreases_teaching_error_for_an_expected_reward():
    b=CompartmentBrain();obs=scenario().observation()
    first=None
    for _ in range(50):
        d=b.decide(obs,0);result=b.reinforce(d,1,.5)
        if first is None:first=result
    assert result['dan_plus']<first['dan_plus']
    config=CircuitSettings(feedback=False)
    d=b.decide(obs,0,config);r=b.reinforce(d,1,.5)
    assert all(c['delta']==1 for c in r['compartments'])


def test_delay_decays_fast_trace_more_than_slow():
    b=CompartmentBrain();d=b.decide(scenario().observation(),0)
    r=b.reinforce(d,1,.25,credit_delay=16)
    traces=[c['eligibility'] for c in r['compartments']]
    assert 0<traces[0]<traces[1]<traces[2]<1
    np.testing.assert_allclose(traces,np.exp(-16/np.array([4.,16.,64.])))


def test_delay_ablation_is_intact_credit_even_for_long_delay():
    a,b=CompartmentBrain(),CompartmentBrain()
    da=a.decide(scenario().observation(),0)
    db=b.decide(scenario().observation(),0,CircuitSettings(eligibility=False))
    a.reinforce(da,1,.25,credit_delay=0)
    r=b.reinforce(db,1,.25,credit_delay=240)
    np.testing.assert_array_equal(a.w_plus,b.w_plus)
    assert all(c['eligibility']==1 for c in r['compartments'])


def test_freeze_also_freezes_forgetting():
    b=CompartmentBrain();obs=scenario().observation()
    d=b.decide(obs,0);b.reinforce(d,1,.5)
    d=b.decide(obs,0,CircuitSettings(forgetting=True))
    before=b.to_dict();r=b.reinforce(d,-1,.25,enabled=False,credit_delay=10)
    assert b.to_dict()==before and not r['updated']
    assert r['decay_change']==r['weight_change']==0
    assert r['dan_minus']>0


def test_optional_forgetting_changes_unselected_memories():
    b=CompartmentBrain();d=b.decide(scenario().observation(),0)
    b.reinforce(d,1,.5)
    d=b.decide(scenario().observation(),0,CircuitSettings(forgetting=True))
    d=replace(d,action=(d.action+1)%3)
    before=b.w_plus.copy();r=b.reinforce(d,-1,.25)
    assert r['decay_change']>0
    assert np.any(b.w_plus[:,:, (d.action-1)%3] != before[:,:, (d.action-1)%3])


def test_animation_speed_not_a_neural_parameter():
    a,b=CompartmentBrain(),CompartmentBrain()
    sa=scenario(duration=1);sb=scenario(duration=60)
    da,db=a.decide(sa.observation(),0),b.decide(sb.observation(),0)
    a.reinforce(da,1,.25);b.reinforce(db,1,.25)
    assert a.to_dict()==b.to_dict()


@pytest.mark.parametrize('seed',[0,1,42,134])
def test_repeated_association_then_reversal_is_learnable_on_toy_task(seed):
    b=CompartmentBrain(seed);obs=scenario().observation()
    for label in (0,2):
        actions=[]
        for _ in range(70):
            d=b.decide(obs,0);actions.append(d.action)
            b.reinforce(d,1 if d.action==label else -1,.25)
        assert actions[-10:]==[label]*10
    assert np.all((b.w_plus>=0)&(b.w_plus<=1))


def test_save_restore_reproduces_weights_rng_and_next_decisions():
    b=CompartmentBrain(8);obs=scenario().observation()
    d=b.decide(obs,.2);b.reinforce(d,-1,.3)
    restored=CompartmentBrain.from_dict(json.loads(json.dumps(b.to_dict())))
    for _ in range(10):
        da,db=b.decide(obs,.3),restored.decide(obs,.3)
        assert da.action==db.action and da.extra==db.extra
        np.testing.assert_array_equal(da.kc,db.kc)
        b.reinforce(da,1,.2);restored.reinforce(db,1,.2)
    assert b.to_dict()==restored.to_dict()


@pytest.mark.parametrize('field,value',[('w_plus',[[1]]),('input_indices',[[999999]]),('updates',-1)])
def test_corrupt_checkpoint_is_rejected(field,value):
    b=CompartmentBrain();raw=b.to_dict();raw[field]=value
    with pytest.raises(ValueError):CompartmentBrain.from_dict(raw)


def test_real_alpha01_checkpoint_migration_preserves_old_memory(tmp_path):
    old=MushroomBody(98);d=old.decide(scenario().observation(),0);old.reinforce(d,1,.5)
    raw={'brain':old.to_dict(),'counter':12,'trials':[]}
    original=json.dumps(raw);(tmp_path/'state.json').write_text(original)
    lab=Lab(tmp_path)
    assert lab.active_model=='compartments' and lab.brain.updates==0
    assert lab.models['legacy'].to_dict()==old.to_dict()
    copies=list(tmp_path.glob('archive-migration-alpha01-*.json'))
    assert len(copies)==1 and copies[0].read_text()==original
    lab.switch('legacy');assert lab.brain.to_dict()==old.to_dict()
    restored=Lab(tmp_path)
    assert restored.active_model=='legacy'
    assert len(list(tmp_path.glob('archive-migration-alpha01-*.json')))==1


def test_separate_models_and_reset_only_active(tmp_path):
    lab=Lab(tmp_path);lab.start(scenario(duration=1),now=0);lab.tick(2)
    extended=lab.brain.to_dict()
    lab.switch('legacy');lab.start(scenario(duration=1),now=0);lab.tick(2)
    legacy=lab.brain.to_dict()
    assert lab.models['compartments'].to_dict()==extended
    assert all(m['stats']['trials']==1 for m in lab.snapshot()['models'])
    lab.reset();assert lab.models['compartments'].to_dict()==extended
    assert lab.snapshot()['stats']['trials']==0
    lab.switch('compartments');assert lab.snapshot()['stats']['trials']==1
    assert lab.counter==2


def test_cannot_switch_during_trial(tmp_path):
    lab=Lab(tmp_path);lab.start(scenario(),now=0)
    with pytest.raises(ValueError):lab.switch('legacy')
    assert lab.active_model=='compartments'


def test_polling_is_read_only_for_weights_and_rng(tmp_path):
    lab=Lab(tmp_path);lab.start(scenario(credit_delay=50),now=0)
    before=lab.brain.to_dict()
    for t in [0,.5,1,2]:lab.snapshot(now=t)
    assert lab.brain.to_dict()==before


def test_start_storage_failure_rolls_back_rng_and_counter(tmp_path,monkeypatch):
    lab=Lab(tmp_path);before=lab.brain.to_dict()
    def fail():raise OSError('disk full')
    monkeypatch.setattr(lab,'save',fail)
    with pytest.raises(OSError):lab.start(scenario(),now=0)
    assert lab.counter==0 and lab.run is None and lab.brain.to_dict()==before


def test_model_switch_storage_failure_rolls_back_selection(tmp_path,monkeypatch):
    lab=Lab(tmp_path)
    def fail():raise OSError('disk full')
    monkeypatch.setattr(lab,'save',fail)
    with pytest.raises(OSError):lab.switch('legacy')
    assert lab.active_model=='compartments'


def test_reset_storage_failure_keeps_original_memory(tmp_path,monkeypatch):
    lab=Lab(tmp_path);lab.start(scenario(duration=1),now=0);lab.tick(2)
    before=lab.brain.to_dict();trials=lab.trials.copy()
    def fail():raise OSError('disk full')
    monkeypatch.setattr(lab,'save',fail)
    with pytest.raises(OSError):lab.reset()
    assert lab.brain.to_dict()==before and lab.trials==trials
