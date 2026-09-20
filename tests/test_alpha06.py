import copy,json,math,time
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app.brain06 import Brain06
from app.economy06 import Rules06,quotes06,choose06,net_result,financial_signal,STAKES,Calibrator
from app.market import Market
from app.orderbook import OrderBook
from app.run06 import Run06
from app.academy06 import Academy06,Config06,validate_row
from app.live import GridObservation


def obs():return GridObservation(tuple(3000+np.linspace(0,.8,31)),.5/3000*100,18,.4,3000.2)
def probs():return [{'p':.8,'lower':.65,'n':100,'ready':True} for _ in range(3)]
def market():
    m=Market();m.begin(now=100)
    for i in range(33):m.receive({'kind':'trade','time':1000+i,'id':100+i,'price':3000.2},now=100+i)
    return m

def advance(run,t,price=3000.2):
    m=run.market
    start=math.floor(m.watermark)+1
    for x in range(start,math.floor(t)+1):
        m.receive({'kind':'trade','time':float(x),'id':m.last_id+1,'price':price},now=x-900)
        run.observe(m.ticks[-1]);run.lock_ready(x-900);run.finish_ready()
    return t-900

@pytest.mark.parametrize('n',[1024,2048])
@pytest.mark.parametrize('sparsity',[.02,.05,.1,.2])
def test_kc_count_roundtrip(n,sparsity):
    b=Brain06(n_kc=n,sparsity=sparsity);d=b.predict(obs())
    assert abs(np.count_nonzero(d.kc)-n*sparsity)<=1
    assert b.fingerprint()==Brain06.from_dict(b.to_dict()).fingerprint()
    assert len(b.neural(d)['kc_frequency'])==n

def test_value_learning_does_not_change_forecast_or_calibration_identity():
    b=Brain06();d=b.predict(obs());before=b.fingerprint();w=b.weights.copy()
    z=b.learn_financial(d,[2,-1,None]);assert z['updated'] and b.value_updates==1
    assert b.fingerprint()==before and np.array_equal(w,b.weights)
    assert z['signals'][0]['target']>0>z['signals'][1]['target']

def test_empty_financial_targets_no_update():
    b=Brain06();d=b.predict(obs());assert not b.learn_financial(d,[None]*3)['updated']

def test_prediction_is_causal_without_targets():
    b=Brain06();x=obs();d=b.predict(x);b2=Brain06();assert np.allclose(d.scores,b2.predict(x).scores)
    b.learn_outcomes(d,[True,False,False],signal='all');assert b.updates==1

@pytest.mark.parametrize('key,value',[('n_kc',999),('value_weights',[float('nan')]*2048),('weights',[[.5]*3]*5)])
def test_invalid_checkpoint(key,value):
    d=Brain06().to_dict();d[key]=value
    with pytest.raises(ValueError):Brain06.from_dict(d)

@pytest.mark.parametrize('stake',STAKES)
def test_payoff(stake):
    assert net_result(True,3,stake,0)==pytest.approx(2*stake)
    assert net_result(False,3,stake,.01)==pytest.approx(-1.01*stake)

@pytest.mark.parametrize('delay',[0,1,2,5])
def test_quotes_locked_fixed_geometry(delay):
    rules=Rules06(execution_delay=delay)
    q=quotes06([3000.2]*31,3000.2,1050,1032,6000,rules)
    q2=quotes06([3000.8]*31,3000.8,1050,1032+delay,6000,rules)
    assert q2['base_row']==q['base_row']==6000
    assert q2['gross']!=q['gross']
    assert max(x for x in q['gross'] if x is not None)<=12
    assert 'NOT_EUPHORIA' in q['source']

def test_manual_convention_is_explicit():
    rules=Rules06(quote_mode='manual',convention='profit',multipliers=[2,3,4])
    assert quotes06([3]*31,3,30,10,6,rules)['gross']==[3,4,5]

def test_no_fabricated_positive_paying_near_certain_event():
    q=quotes06([3000.25]*31,3000.25,20,19.99,6000,Rules06(volatility_floor=.001))
    assert q['gross'][1] is None

@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-2])
def test_bad_rules(bad):
    with pytest.raises(ValueError):Rules06(execution_delay=bad)

def test_sizing_under_budget():
    rules=Rules06(quote_mode='manual',multipliers=[5,5,5],max_fraction=.05)
    q=quotes06([3000]*31,3000.2,1050,1032,6000,rules)
    p=choose06(probs(),q,rules,20,20,enabled=True)
    assert p['stake'] in (.1,1.) and p['reserved_cost']<=1
    assert choose06(probs(),q,rules,20,20,reserved=1,enabled=True)['chosen'] is None

def test_all_in_excluded_even_when_ceiling_100pct():
    rules=Rules06(quote_mode='manual',multipliers=[100]*3,max_fraction=1,fixed_stake=20,stake_mode='fixed')
    q=quotes06([3]*31,3,30,10,6,rules)
    assert choose06(probs(),q,rules,20,20,enabled=True)['chosen'] is None

@pytest.mark.parametrize('enabled,capital,peak,feature',[(False,20,20,True),(True,0,20,True),(True,15,20,True),(True,20,20,False)])
def test_wait_guards(enabled,capital,peak,feature):
    rules=Rules06(quote_mode='manual',multipliers=[5]*3);q=quotes06([3]*31,3,30,10,6,rules)
    assert choose06(probs(),q,rules,capital,peak,enabled=enabled,feature_ready=feature)['chosen'] is None

def test_financial_signal_size_sensitive():
    assert 0<financial_signal(.1,20)['dan_gain']<financial_signal(1,20)['dan_gain']
    assert financial_signal(-1,20)['dan_loss']>0
    assert not financial_signal(-1,20)['weights_changed']

def funded_run(tmp_path):
    r=Run06(tmp_path,market(),OrderBook());r.rules=Rules06(quote_mode='manual',multipliers=[3]*3)
    r.calibrator.predict=lambda *args:probs();r.policy_enabled=True
    return r

def test_delayed_execution_and_single_settlement(tmp_path):
    r=funded_run(tmp_path);x=r.start(now=132);assert x['order_status']=='submitted' and r.reserved>0
    assert x['start']-x['lock_due']>=10
    fixed=x['base_row'];a=x['chosen'];p=(fixed+(1,0,-1)[a]+.3)*.5
    advance(r,x['lock_due'],price=p)
    assert x['order_status']=='locked' and x['base_row']==fixed
    advance(r,x['end']+1,price=p);assert r.stats['wins']==1 and r.reserved==0
    assert r.capital==pytest.approx(20+net_result(True,x['quote_lock']['effective_gross'][a],x['stake'],0))
    net=r.stats['net'];r.finish_ready();assert r.stats['net']==net
    assert r.brain.updates==0;r.close()

def test_adverse_slippage_rejects_and_refunds(tmp_path):
    r=funded_run(tmp_path);x=r.start(now=132)
    # Recorded settings mutate ONLY inside test to simulate worse execution quote.
    x['settings']['multipliers']=[1.1]*3
    advance(r,x['lock_due']);assert x['order_status']=='rejected' and r.reserved==0
    advance(r,x['end']+1);assert r.stats['trades']==0 and r.capital==20;r.close()

def test_late_lock_rejected(tmp_path):
    r=funded_run(tmp_path);x=r.start(now=132)
    r.market.receive({'kind':'trade','time':x['start']-9,'id':r.market.last_id+1,'price':3000.2},now=x['start']-909)
    r.lock_ready(x['start']-909)
    assert x['order_status']=='rejected';r.close()

def test_empty_window_void_no_loss(tmp_path):
    r=funded_run(tmp_path);x=r.start(now=132);advance(r,x['lock_due'])
    r.market.watermark=x['end']+1;r.finish_ready();assert r.capital==20 and r.stats['void']==1;r.close()

def test_disconnect_stops_policy_not_collector(tmp_path):
    r=funded_run(tmp_path);r.collect=True;r.start(now=132);r.invalidate('testgap')
    assert r.collect and not r.policy_enabled and r.reserved==0 and r.capital==20;r.close()

def test_restart_preserves_collector_voids_pending(tmp_path):
    r=funded_run(tmp_path);r.collect=True;r.start(now=132);r.close()
    r=Run06(tmp_path,Market(),OrderBook());assert r.collect and not r.policy_enabled
    assert not r.pending and r.stats['void']==1;r.close()

def test_raw_data_persist_even_when_portfolio_exhausted(tmp_path):
    r=Run06(tmp_path,market(),OrderBook());r.stats['net']=-20;r.controls(collect=True)
    r.observe(r.market.ticks[-1]);r.flush_raw();assert r.db.execute('SELECT COUNT(*) FROM ticks').fetchone()[0]==1
    r.start(now=132);assert r.last['chosen'] is None;r.close()

def test_reset_archives_not_corpus(tmp_path):
    r=Run06(tmp_path,market(),OrderBook());r.start(now=132);advance(r,r.last['end']+1)
    n=len(r.export_rows());r.reset_wallet();assert r.capital==20 and len(r.export_rows())==n
    assert r.db.execute('SELECT COUNT(*) FROM archives').fetchone()[0]==1;r.close()

def test_settings_guard(tmp_path):
    r=Run06(tmp_path,market(),OrderBook());r.start(now=132)
    with pytest.raises(ValueError):r.configure(Rules06())
    r.close()

def rows(n=90,delay=2):
    for i in range(n):
        placed=10000+i*60;start=placed+15;touch=[i%3==a for a in range(3)]
        p=100.2+(1,0,-1)[i%3]*.5
        yield dict(schema06=1,id=i,status='observed',source='test',placed=placed,start=start,end=start+5,
            history_end=placed,history=[100.2]*31,reference=100.2,base_row=200,chosen=None,
            touches=touch,window_ticks=[[start+1,p,i]],book=None,quote_lock=None)

def test_training_shuffle_no_test_leakage_and_persistence(tmp_path):
    a=Academy06(tmp_path);a.import_lines(json.dumps(x) for x in rows(120))
    a.create(Config06(n_kc=2048,epochs=1,mode='chronological',use_liquidity=False,shuffle_train=True))
    p=a.plan;train=[uid for phase,uid in p if phase.startswith('Train')]
    assert train!=a.splits['train'] and set(train)==set(a.splits['train'])
    assert [uid for phase,uid in p if phase=='Calibration gelee']==a.splits['validation']
    assert set(train).isdisjoint(a.splits['test']);assert a.snapshot()['split']['splits']['test']['hidden']
    while a.position<len(a.plan):a.step_batch(20)
    f=a.brain.fingerprint();a.open_test()
    while a.position<len(a.plan):a.step_batch(20)
    assert a.brain.fingerprint()==f
    before=a.position;a.close();a=Academy06(tmp_path);assert a.position==before and not a.auto;a.close()

def test_recorded_quotes_only_for_financial_head(tmp_path):
    rr=list(rows(100));q=quotes06(rr[0]['history'],100.2,rr[0]['start'],rr[0]['placed']+2,200,Rules06(quote_mode='manual'))
    rr[0]['quote_lock']=q
    a=Academy06(tmp_path);report=a.import_lines(json.dumps(x) for x in rr)
    assert report['with_delayed_quotes']==1
    a.create(Config06(epochs=1,mode='chronological',use_liquidity=False))
    a.step_batch(1);assert a.brain.value_updates==1;a.close()

def test_import_dedup_and_contradictions(tmp_path):
    a=Academy06(tmp_path);rr=list(rows(30));a.import_lines(json.dumps(r) for r in rr)
    report=a.import_lines(json.dumps(r) for r in rr);assert report['added']==0
    rr[0]['history'][0]+=1
    report=a.import_lines([json.dumps(rr[0])]);assert report['rejected']['doublon_contradictoire']==1;a.close()

def test_bad_quote_bounds_and_noncausal_book():
    r=next(rows());q=quotes06(r['history'],r['reference'],r['start'],r['placed']+2,201,Rules06());r['quote_lock']=q
    with pytest.raises(ValueError):validate_row(r)

def test_http_endpoints_csrf_and_fresh_start(tmp_path,monkeypatch):
    monkeypatch.setenv('FLYTRADE_DATA',str(tmp_path));monkeypatch.setenv('FLYTRADE_FEED','off')
    from app.main import app
    with TestClient(app) as c:
        assert c.get('/health').json()['version']=='0.8.0-alpha'
        assert c.get('/api/state').json()['stats']['capital']==20
        assert c.get('/api/state').json()['neural']['n_kc']==2048
        assert c.get('/').status_code==200
        assert c.post('/api/reset',json={'confirm':True},headers={'Origin':'http://evil.example'}).status_code==403
        assert c.post('/api/training/deploy',json={'confirm':True}).status_code==409
        assert c.post('/api/training/import',content='\n'.join(json.dumps(r) for r in rows())).status_code==200
        assert c.post('/api/training/create',json={'mode':'chronological','use_liquidity':False,'epochs':1}).status_code==200
        assert c.post('/api/training/control',json={'action':'step'}).status_code==200
        assert c.get('/api/training/report').json()['position']==1
        assert c.get('/api/export/windows').status_code==200

def test_one_window_per_slot_not_minimum_five_seconds(tmp_path):
    r=Run06(tmp_path,market(),OrderBook())
    r.start(now=132.8) # 1032.8, slot 1030
    r.market.receive({'kind':'clock','time':1034.,'id':r.market.last_id},now=134.)
    r.market.receive({'kind':'trade','time':1035.1,'id':r.market.last_id+1,'price':3000.2},now=135.1)
    r.start(now=135.1) # new slot 1035, even <5 s since previous call
    assert len(r.pending)==2;r.close()

def test_deploy_does_not_touch_wallet_on_mismatch(tmp_path):
    r=Run06(tmp_path,market(),OrderBook());r.stats['net']=2
    with pytest.raises(ValueError):r.deploy(Brain06(),Calibrator(),{})
    assert r.capital==22 and r.generation==1;r.close()

def test_no_old_state_used(tmp_path):
    (tmp_path/'state.json').write_text('{"some_old_version":true}')
    r=Run06(tmp_path,Market(),OrderBook());assert r.capital==20 and r.brain.n_kc==2048
    assert (tmp_path/'state.json').read_text()=='{"some_old_version":true}';r.close()
