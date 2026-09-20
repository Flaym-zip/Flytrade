"""Phase 1.2 training wizard: one named brain trained end to end through HTTP,
without exposing session/academy/replay/checkpoint/lineage to the caller.
"""
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from app.academy06 import Config06
from app.academy08 import Academy08
from test_phase0 import lines, mkrow


async def idle(*args, **kw):
    await asyncio.Event().wait()


def make_client(monkeypatch, tmp_path):
    import app.main as main
    monkeypatch.setenv('FLYTRADE_DATA', str(tmp_path))
    return TestClient(main.app)


def create_brain(c, name='Fly-Test', **kw):
    kw.setdefault('n_kc', 1024)
    kw.setdefault('use_liquidity', False)
    return c.post('/api/brains', json=dict(name=name, **kw)).json()


def import_dataset(c, n=120, source='src', name='Src1'):
    body = '\n'.join(lines(n, source=source))
    return c.post('/api/training/import?name=' + name, content=body).json()


def default_config(dataset_id, **overrides):
    cfg = dict(seed=42, n_kc=2048, mode='chronological', epochs=1, shuffle_train=True,
               signal='all', learning_rate=.25, epsilon=.15, sparsity=.05, use_liquidity=False,
               economic_head=False, dataset_id=dataset_id, max_windows=10000, batch=10)
    cfg.update(overrides)
    return cfg


def run_all_steps(c, workshop_id, snap):
    while snap['position'] < snap['total']:
        snap = c.post(f'/api/brains/{workshop_id}/train/step', json={'batch': 50}).json()
    return snap


def test_brain_selected_correctly(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Fly-Kraken-2100-A')
        got = c.get('/api/brains/' + b['workshop_id']).json()
        assert got['name'] == 'Fly-Kraken-2100-A' and got['workshop_id'] == b['workshop_id']
        assert got['brain_id'] == b['brain_id']


def test_selected_brain_keeps_its_brain_id_through_first_training_when_architecture_matches(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Fly-Kraken-2100-A', n_kc=1024, seed=7, sparsity=.05, use_liquidity=False)
        imp = import_dataset(c)
        # The wizard's own protocol form never sends the brain's real n_kc/seed/etc (the
        # server always overrides them from the brain itself), but even a client that DID
        # send matching values must not cause an identity swap.
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare",
                       json=default_config(imp['dataset_id'], n_kc=1024, seed=7, sparsity=.05, use_liquidity=False)).json()
        assert prep['brain_id'] == b['brain_id']
        snap = run_all_steps(c, b['workshop_id'], prep)
        assert snap['brain_id'] == b['brain_id']
        got = c.get('/api/brains/' + b['workshop_id']).json()
        assert got['brain_id'] == b['brain_id'] and got['updates'] > 0


def test_duplicated_brain_keeps_learning_from_its_copied_weights_not_a_new_brain(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        src = create_brain(c, 'Source', n_kc=1024)
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{src['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, src['workshop_id'], prep)
        src_trained = c.get('/api/brains/' + src['workshop_id']).json()
        assert src_trained['updates'] > 0
        dup = c.post(f"/api/brains/{src['workshop_id']}/duplicate", json={'name': 'Clone'}).json()
        assert dup['brain_id'] != src_trained['brain_id']  # new permanent identity
        assert dup['weight_fingerprint'] == src_trained['weight_fingerprint']  # weights copied as-is
        assert dup['updates'] == src_trained['updates']  # not reset to a blank brain
        # Continuing to train the duplicate must retain ITS identity and build on the
        # copied weights, not silently swap in a fresh random brain.
        dup_prep = c.post(f"/api/brains/{dup['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        assert dup_prep['brain_id'] == dup['brain_id']
        dup_snap = run_all_steps(c, dup['workshop_id'], dup_prep)
        assert dup_snap['brain_id'] == dup['brain_id']
        dup_after = c.get('/api/brains/' + dup['workshop_id']).json()
        assert dup_after['updates'] > src_trained['updates']  # learning continued past the clone point
        src_after = c.get('/api/brains/' + src['workshop_id']).json()
        assert src_after['updates'] == src_trained['updates']  # source untouched by the clone's training


def test_dataset_selected_correctly(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        session = c.get(f"/api/brains/{b['workshop_id']}/session").json()
        assert session['config']['dataset_id'] == imp['dataset_id']
        assert prep['config']['dataset_id'] == imp['dataset_id']


def test_wrong_dataset_id_refused(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        r = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config('not-a-real-dataset'))
        assert r.status_code >= 400


def test_wrong_source_dataset_refused_at_academy_level(tmp_path):
    # Two provider contexts sharing one data dir: a dataset imported under one
    # source must never be trainable from an academy locked to a different source.
    other = Academy08(tmp_path, expected_source='kraken-ETH-USD')
    other.import_lines((json.dumps(x) for x in [mkrow(i, source='kraken-ETH-USD') for i in range(20)]))
    other.close()
    locked = Academy08(tmp_path, expected_source='coinbase-ETH-USD')
    dataset_id = next(d['dataset_id'] for d in locked.datasets() if d['source'] == 'kraken-ETH-USD')
    with pytest.raises(ValueError):
        locked.create(Config06(n_kc=1024, epochs=1, mode='chronological', use_liquidity=False, dataset_id=dataset_id))
    locked.close()


def test_workflow_70_15_15(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c, n=200)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        splits = prep['split']['splits']
        total = sum(v['n'] for v in splits.values())
        assert abs(splits['train']['n']/total - .70) < .03
        assert abs(splits['validation']['n']/total - .15) < .03
        assert abs(splits['test']['n']/total - .15) < .03
        assert splits['test']['hidden'] is True  # never revealed before test/open


def test_train_required_before_validation(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c)
        c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id']))
        m = c.get(f"/api/brains/{b['workshop_id']}/metrics").json()
        assert 'validation' not in m  # TRAIN/CAL not yet run
        prep = c.get(f"/api/brains/{b['workshop_id']}/session").json()
        run_all_steps(c, b['workshop_id'], prep)
        m2 = c.get(f"/api/brains/{b['workshop_id']}/metrics").json()
        assert 'validation' in m2 and m2['validation']['n'] > 0


def test_test_impossible_before_training_finished(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c)
        c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id']))
        r = c.post(f"/api/brains/{b['workshop_id']}/test/open")
        assert r.status_code >= 400


def test_test_opens_only_once(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, b['workshop_id'], prep)
        first = c.post(f"/api/brains/{b['workshop_id']}/test/open")
        assert first.status_code == 200
        second = c.post(f"/api/brains/{b['workshop_id']}/test/open")
        assert second.status_code >= 400


def test_test_exposure_survives_restart(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, b['workshop_id'], prep)
        c.post(f"/api/brains/{b['workshop_id']}/test/open")
    with make_client(monkeypatch, tmp_path) as c2:
        got = c2.get('/api/brains/' + b['workshop_id']).json()
        assert got['test_opened'] is True
        assert c2.post(f"/api/brains/{b['workshop_id']}/test/open").status_code >= 400


def test_multiple_and_none_excluded_from_hsb_metrics(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        rows_ = [mkrow(i) for i in range(28)]
        rows_.append(mkrow(28, prices=[110.]))          # aucune
        rows_.append(mkrow(29, prices=[100.7, 99.7]))    # multiple
        body = '\n'.join(json.dumps(r) for r in rows_)
        imp = c.post('/api/training/import?name=Mixed', content=body).json()
        assert imp['windows'] == 30
        ds = next(d for d in c.get('/api/datasets').json() if d['dataset_id'] == imp['dataset_id'])
        assert ds['stats']['aucune'] == 1 and ds['stats']['multiple'] == 1
        # Not enough single-touch rows for a 70/15/15 split (need >=15) with only 28 left after
        # excluding the 2 ambiguous ones is still workable; assert the plan only uses single-touch uids.
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        snap = run_all_steps(c, b['workshop_id'], prep)
        assert snap['corpus']['windows'] == 30  # dataset untouched
        assert snap['split']['splits']['train']['n'] + snap['split']['splits']['validation']['n'] + \
               snap['split']['splits']['test']['n'] <= 28


def test_balanced_accuracy_displayed_correctly(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c)
        imp = import_dataset(c, n=200)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, b['workshop_id'], prep)
        m = c.get(f"/api/brains/{b['workshop_id']}/metrics").json()
        v = m['validation']
        present = [r for r in v['recall'] if r is not None]
        assert v['balanced_accuracy'] == pytest.approx(sum(present)/len(present))
        assert 'hasard' in v['baselines'] and v['baselines']['hasard']['balanced_accuracy'] == pytest.approx(1/3)


def test_training_brain_a_does_not_modify_brain_b(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        a = create_brain(c, 'A')
        b = create_brain(c, 'B')
        imp = import_dataset(c)
        before_b = c.get('/api/brains/' + b['workshop_id']).json()
        prep = c.post(f"/api/brains/{a['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, a['workshop_id'], prep)
        after_a = c.get('/api/brains/' + a['workshop_id']).json()
        after_b = c.get('/api/brains/' + b['workshop_id']).json()
        assert after_a['updates'] > 0
        assert after_b['updates'] == 0
        assert after_b['brain_id'] == before_b['brain_id']
        assert after_b['weight_fingerprint'] == before_b['weight_fingerprint']


def test_train_button_from_brains_page_preselects_brain():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / 'app/static/brains.js').read_text()
    assert "'/entrainement?workshop=' + b.workshop_id" in js
    wizard_js = (Path(__file__).resolve().parents[1] / 'app/static/wizard.js').read_text()
    assert 'URLSearchParams' in wizard_js and "get('workshop')" in wizard_js


def test_wizard_page_preselects_brain_via_query_param(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Preselected')
        page = c.get('/entrainement?workshop=' + b['workshop_id'])
        assert page.status_code == 200
        assert c.get('/brains').status_code == 200
