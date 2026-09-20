"""Lifecycle invariants. No external API and no modification to neuron formulas."""
import copy,json,sqlite3
from pathlib import Path
import numpy as np
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from app.academy06 import Academy06,Config06
from app.academy08 import Academy08,CAL,TEST
from app.brain06 import Brain06
from app.run08 import Run08
from app.market import Market
from app.orderbook import OrderBook
from test_alpha06 import rows,obs

ROOT=Path(__file__).resolve().parents[1]
CFG=Config06(n_kc=1024,epochs=1,mode='chronological',use_liquidity=False)

def make(tmp_path,n=100):
    a=Academy08(tmp_path);a.import_lines(json.dumps(x) for x in rows(n));return a

def weights(d):
    """Weight identity, excluding brain_id: same params still get distinct permanent ids."""
    d=copy.deepcopy(d);d.pop('brain_id',None);return d

def finish(a):
    while a.position<len(a.plan):a.step_batch(40)

def test_preview_never_mutates_any_checkpoint_or_archive(tmp_path):
    a=make(tmp_path);before=a.checkpoint();n=a.db.execute('SELECT COUNT(*) FROM archives').fetchone()[0]
    p=a.preview(CFG)
    assert p['eligible']==100 and p['split']['splits']['test']['hidden']
    assert set(p['split']['splits']['test'])=={'n','hidden'}
    assert p['presentations']>p['train_presentations'] and p['calibration_n']<60
    assert a.checkpoint()==before and a.db.execute('SELECT COUNT(*) FROM archives').fetchone()[0]==n;a.close()

@pytest.mark.parametrize('mode',['chronological','balanced','curriculum'])
def test_initial_plan_and_numerical_updates_identical_to_alpha07(tmp_path,mode):
    one=tmp_path/'old';two=tmp_path/'new';one.mkdir();two.mkdir();old=Academy06(one);new=Academy08(two)
    data=[json.dumps(x) for x in rows(90)];old.import_lines(data);new.import_lines(data)
    cfg=CFG.model_copy(update={'mode':mode,'shuffle_train':True,'epochs':2})
    old.create(cfg);new.create(cfg)
    assert old.plan==new.plan and old.splits==new.splits
    old.step_batch(20);new.step_batch(20)
    assert old.brain.brain_id!=new.brain.brain_id
    assert weights(old.brain.to_dict())==weights(new.brain.to_dict())
    old.close();new.close()

def test_replay_reuses_exact_plan_and_reinitializes_weights(tmp_path):
    a=make(tmp_path);a.create(CFG);plan=copy.deepcopy(a.plan);initial=a.brain.to_dict();session=a.session
    a.step_batch(8);assert a.brain.updates>0
    trained=a.brain.to_dict();a.replay()
    assert a.session!=session and a.plan==plan and a.position==0 and not a.auto
    assert a.brain.brain_id!=initial['brain_id']
    assert weights(a.brain.to_dict())==weights(initial) and a.calibrator.n==0
    assert a.lineage['parent']==session and a.lineage['mode']=='replay'
    archive=a.archive(session);assert archive['brain']==trained and archive['position']==8
    a.close()

def test_resume_advances_instead_of_reset_and_restart_keeps_position(tmp_path):
    a=make(tmp_path);a.create(CFG);a.step_batch(5);finger=a.brain.fingerprint();a.close()
    a=Academy08(tmp_path);assert a.position==5 and a.brain.fingerprint()==finger and not a.auto
    a.step_batch(1);assert a.position==6 and a.brain.updates==6;a.close()

def test_continue_keeps_weights_and_uses_only_saved_train_ids(tmp_path):
    a=make(tmp_path);a.create(CFG);finish(a);before=a.brain.to_dict();oldids=copy.deepcopy(a.splits)
    a.open_test();finish(a);before=a.brain.to_dict();parent=a.session
    a.continue_training(2)
    assert a.brain.to_dict()==before and a.calibrator.n==0
    assert a.lineage['initial_updates']==before['updates'] and a.lineage['parent']==parent
    assert a.splits==oldids
    train=[uid for phase,uid in a.plan if phase!=CAL]
    assert set(train)==set(oldids['train']) and len(train)==2*len(oldids['train'])
    assert not set(train)&(set(oldids['test'])|set(oldids['validation']))
    assert a.snapshot()['test_reused']==len(oldids['test'])
    updates=a.brain.updates;finish(a);assert a.brain.updates==updates+len(train)
    a.close()

def test_continue_refuses_unfinished_without_mutation(tmp_path):
    a=make(tmp_path);a.create(CFG);a.step_batch(2);before=a.checkpoint()
    with pytest.raises(ValueError):a.continue_training()
    assert a.checkpoint()==before;a.close()

def test_continue_does_not_include_new_imports(tmp_path):
    a=make(tmp_path,90);a.create(CFG);finish(a);old=copy.deepcopy(a.splits)
    a.import_lines(json.dumps(x) for x in list(rows(120))[90:]);assert a.corpus_info()['windows']==120
    a.continue_training();assert a.splits==old;a.close()

def test_test_exposure_survives_reset_and_recreation_and_restart(tmp_path):
    a=make(tmp_path);a.create(CFG);finish(a);a.open_test();uids=a.splits['test'];a.reset_training(CFG)
    assert a.corpus_info()['windows']==100 and a.preview(CFG)['reused_test']==len(uids)
    a.close();a=Academy08(tmp_path);assert a.preview(CFG)['reused_test']==len(uids)
    a.create(CFG);assert a.snapshot()['test_reused']==len(uids);a.close()

def test_old_version_test_exposure_backfilled(tmp_path):
    a=Academy06(tmp_path);a.import_lines(json.dumps(x) for x in rows());a.create(CFG);finish(a);a.open_test()
    before=a.brain.to_dict();n=len(a.splits['test']);a.close()
    a=Academy08(tmp_path);assert a.brain.to_dict()==before and a.snapshot()['test_reused']==n;a.close()

def test_reset_training_keeps_corpus_and_archives_all_weights(tmp_path):
    a=make(tmp_path);a.create(CFG);a.step_batch(3);b=copy.deepcopy(a.brain.to_dict());session=a.session
    cfg=CFG.model_copy(update={'n_kc':2048,'seed':100})
    a.reset_training(cfg)
    assert weights(a.brain.to_dict())==weights(Brain06(n_kc=2048,seed=100,use_liquidity=False).to_dict())
    assert a.session is None and not a.plan and a.corpus_info()['windows']==100
    assert a.archive(session)['brain']==b;a.close()

def test_failed_lifecycle_write_rolls_back_runtime(tmp_path):
    a=make(tmp_path);a.create(CFG);a.step_batch(4);before=a.checkpoint()
    a.db.execute("CREATE TRIGGER fail_reset BEFORE INSERT ON workshop_state BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):a.reset_training(CFG)
    assert a.checkpoint()==before
    a.db.execute('DROP TRIGGER fail_reset');a.close()

@pytest.mark.parametrize('method',['create','replay','reset_training','continue_training'])
def test_busy_actions_refuse_without_modification(tmp_path,method):
    a=make(tmp_path);a.create(CFG);a.auto=True;before=a.checkpoint()
    with pytest.raises(ValueError):getattr(a,method)(CFG) if method in ('create','reset_training') else getattr(a,method)()
    assert a.checkpoint()==before;a.close()

def test_live_reset_only_changes_brain_not_money(tmp_path):
    r=Run08(tmp_path,Market(),OrderBook());r.stats['net']=3.5;r.stats['trades']=4;r.stats['wins']=2
    r.brain.learn_outcomes(r.brain.predict(obs()),[True,False,False]);stats=copy.deepcopy(r.stats);old=r.brain.to_dict()
    r.reset_brain_only(CFG)
    assert r.stats==stats and r.capital==23.5 and r.brain.updates==0 and r.calibrator.n==0
    assert r.last is None and r.deployment is None
    archive=json.loads(r.db.execute('SELECT payload FROM archives').fetchone()[0]);assert archive['brain']==old;r.close()

@pytest.mark.parametrize('busy',['collect','policy_enabled','pending'])
def test_live_reset_requires_idle(tmp_path,busy):
    r=Run08(tmp_path,Market(),OrderBook());setattr(r,busy,[1] if busy=='pending' else True)
    before=r.brain.fingerprint()
    with pytest.raises(ValueError):r.reset_brain_only(CFG)
    assert r.brain.fingerprint()==before
    r.pending=[];r.close()

def test_http_lifecycle_confirmation_and_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv('FLYTRADE_DATA',str(tmp_path));monkeypatch.setenv('FLYTRADE_FEED','off')
    from app.main import app
    with TestClient(app) as c:
        assert c.get('/guide').status_code==200
        body='\n'.join(json.dumps(x) for x in rows())
        assert c.post('/api/training/import',content=body).status_code==200
        live=c.get('/api/checkpoint').json()
        assert c.post('/api/training/preview',json=CFG.model_dump()).status_code==200
        assert c.get('/api/training/state').json()['session'] is None
        assert c.post('/api/lifecycle',json={'action':'reset_training'}).status_code==409
        assert c.post('/api/training/create',json=CFG.model_dump()).status_code==200
        c.post('/api/training/control',json={'action':'step'})
        before=c.get('/api/training/state').json();assert before['weights_updates']==1
        assert c.post('/api/lifecycle',json={'action':'replay','confirm':True}).status_code==200
        assert c.get('/api/training/state').json()['weights_updates']==0
        assert c.get('/api/checkpoint').json()==live
        arch=c.get('/api/training/archives').json();assert arch
        assert c.get('/api/training/archives/'+arch[0]['id']).status_code==200
        assert c.post('/api/lifecycle',json={'action':'delete','confirm':True}).status_code==422
        assert c.post('/api/lifecycle',json={'action':'reset_live','confirm':True},headers={'Origin':'https://evil.example'}).status_code==403

def test_new_wiki_has_both_levels_and_all_anchors():
    p=BeautifulSoup((ROOT/'app/static/wiki08.html').read_text(),'html.parser');ids=[x['id'] for x in p.select('[id]')]
    assert len(ids)==len(set(ids)) and len(p.select('article.wiki-article'))==29
    assert len(p.select('section.plain08'))>=21
    for link in p.select('a[href^="#"]'):assert link['href'][1:] in ids
    for key in ('debut','debut-reentrainer','debut-reset','plasticite','parametres-train'):
        assert p.find(id=key)
    assert len((ROOT/'docs/wiki/WIKI-08.txt').read_text().split())>16000

def test_tutorial_and_lifecycle_buttons_exist_and_no_mock_runtime():
    p=BeautifulSoup((ROOT/'app/static/index06.html').read_text(),'html.parser');ids=[x['id'] for x in p.select('[id]')]
    assert len(ids)==len(set(ids))
    for key in ['tab-guide','guide-panel','resume-training','replay-training','continue-training','reset-training','reset-live-brain','confirm-dialog','preview-data']:assert key in ids
    for b in p.select('[data-jump]'):assert b['data-jump'] in ids
    scripts=[s.get('src','') for s in p.select('script')]
    assert '/static/clarity08.js' in scripts and not any('support.js' in x for x in scripts)
    assert not any(x.startswith('https:') for x in scripts)
