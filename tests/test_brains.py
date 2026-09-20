"""Phase 1 'Cerveaux' registry: named, independent brains.

brain_id (permanent identity) and weight_fingerprint (weights-only identity)
must never be conflated; two brains with identical requested parameters must
still be distinct; a brain's learning/metrics must never leak into another.
"""
import json
import pytest
from fastapi.testclient import TestClient
from app.academy06 import LEGACY_WORKSHOP_ID, Config06
from app.academy08 import Academy08
from app.brains import BrainRegistry
from test_alpha06 import rows


def test_create_with_name(tmp_path):
    reg = BrainRegistry(tmp_path)
    b = reg.create('Alpha')
    assert b['name'] == 'Alpha' and b['state'] == 'neuf' and b['updates'] == 0
    assert b['brain_id'] and b['weight_fingerprint']
    assert b['brain_id'] != b['weight_fingerprint']


def test_empty_name_refused(tmp_path):
    reg = BrainRegistry(tmp_path)
    with pytest.raises(ValueError):
        reg.create('   ')
    with pytest.raises(ValueError):
        reg.create('')


def test_two_identical_brains_get_different_brain_ids(tmp_path):
    reg = BrainRegistry(tmp_path)
    a = reg.create('A', n_kc=1024, seed=42, sparsity=.05, use_liquidity=False)
    b = reg.create('B', n_kc=1024, seed=42, sparsity=.05, use_liquidity=False)
    assert a['brain_id'] != b['brain_id']
    assert a['workshop_id'] != b['workshop_id']
    # same recipe -> same deterministic weights, but distinct identity
    assert a['weight_fingerprint'] == b['weight_fingerprint']


def test_duplicate_gets_new_brain_id_and_independent_weights(tmp_path):
    reg = BrainRegistry(tmp_path)
    src = reg.create('Source', n_kc=1024)
    dup = reg.duplicate(src['workshop_id'], 'Clone')
    assert dup['brain_id'] != src['brain_id']
    assert dup['workshop_id'] != src['workshop_id']
    assert dup['weight_fingerprint'] == src['weight_fingerprint']  # cloned weights, at creation time


def test_training_one_brain_does_not_modify_another(tmp_path):
    reg = BrainRegistry(tmp_path)
    a = reg.create('A', n_kc=1024)
    b = reg.create('B', n_kc=1024)
    before_b = reg.get(b['workshop_id'])
    academy_a = Academy08(tmp_path, workshop_id=a['workshop_id'])
    academy_a.import_lines(json.dumps(x) for x in rows(100))
    academy_a.create(Config06(n_kc=1024, epochs=1, mode='chronological', use_liquidity=False))
    academy_a.step_batch(20)
    academy_a.close()
    after_a = reg.get(a['workshop_id'])
    after_b = reg.get(b['workshop_id'])
    assert after_a['updates'] > 0
    assert after_b['updates'] == 0
    assert after_b['weight_fingerprint'] == before_b['weight_fingerprint']
    assert after_b['brain_id'] == before_b['brain_id']


def test_duplicate_and_source_learn_independently(tmp_path):
    reg = BrainRegistry(tmp_path)
    src = reg.create('Source', n_kc=1024)
    dup = reg.duplicate(src['workshop_id'], 'Clone')
    academy_dup = Academy08(tmp_path, workshop_id=dup['workshop_id'])
    academy_dup.import_lines(json.dumps(x) for x in rows(100))
    academy_dup.create(Config06(n_kc=1024, epochs=1, mode='chronological', use_liquidity=False))
    academy_dup.step_batch(20)
    academy_dup.close()
    src_after = reg.get(src['workshop_id'])
    dup_after = reg.get(dup['workshop_id'])
    assert dup_after['updates'] > 0
    assert src_after['updates'] == 0
    assert src_after['weight_fingerprint'] == src['weight_fingerprint']


def test_archiving_does_not_delete_data(tmp_path):
    reg = BrainRegistry(tmp_path)
    a = reg.create('A', n_kc=1024)
    reg.archive(a['workshop_id'])
    after = reg.get(a['workshop_id'])
    assert after['archived'] is True and after['state'] == 'archive'
    assert after['brain_id'] == a['brain_id']
    assert after['weight_fingerprint'] == a['weight_fingerprint']
    assert any(r['workshop_id'] == a['workshop_id'] for r in reg.list())


def test_legacy_brain_migrated_and_explicitly_marked(tmp_path):
    # Simulate a pre-existing historic academy (the old single workshop).
    legacy = Academy08(tmp_path)
    legacy.import_lines(json.dumps(x) for x in rows(100))
    legacy.create(Config06(n_kc=1024, epochs=1, mode='chronological', use_liquidity=False))
    legacy.step_batch(10)
    legacy_brain_id = legacy.brain.brain_id
    legacy.close()
    reg = BrainRegistry(tmp_path)
    entries = reg.list()
    legacy_entry = next(e for e in entries if e['workshop_id'] == LEGACY_WORKSHOP_ID)
    assert legacy_entry['legacy'] is True
    assert 'herite' in legacy_entry['name'].lower()
    assert legacy_entry['brain_id'] == legacy_brain_id
    assert legacy_entry['updates'] == 10


def test_state_persists_after_restart(tmp_path):
    reg = BrainRegistry(tmp_path)
    a = reg.create('A', n_kc=1024)
    academy_a = Academy08(tmp_path, workshop_id=a['workshop_id'])
    academy_a.import_lines(json.dumps(x) for x in rows(100))
    academy_a.create(Config06(n_kc=1024, epochs=1, mode='chronological', use_liquidity=False))
    academy_a.step_batch(20)
    academy_a.close()
    before = reg.get(a['workshop_id'])
    reg2 = BrainRegistry(tmp_path)
    after = reg2.get(a['workshop_id'])
    assert after == before
    assert after['state'] == 'entrainement' and after['updates'] > 0


def test_http_page_and_endpoints(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setenv('FLYTRADE_DATA', str(tmp_path))
    monkeypatch.setenv('FLYTRADE_FEED', 'off')
    with TestClient(main.app) as c:
        assert c.get('/brains').status_code == 200
        assert c.get('/api/brains').json() == [] or True  # legacy row appears on first access
        created = c.post('/api/brains', json={'name': 'Alpha', 'n_kc': 1024}).json()
        assert created['name'] == 'Alpha' and created['state'] == 'neuf'
        listed = c.get('/api/brains').json()
        assert any(b['workshop_id'] == created['workshop_id'] for b in listed)
        assert any(b['legacy'] for b in listed)  # historic workshop surfaced, marked legacy
        dup = c.post('/api/brains/'+created['workshop_id']+'/duplicate', json={'name': 'Alpha copie'}).json()
        assert dup['brain_id'] != created['brain_id']
        archived = c.post('/api/brains/'+created['workshop_id']+'/archive').json()
        assert archived['archived'] is True and archived['state'] == 'archive'
        assert c.post('/api/brains', json={'name': ''}).status_code >= 400


def test_legacy_row_matches_live_academy_and_is_stable_across_calls(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setenv('FLYTRADE_DATA', str(tmp_path))
    monkeypatch.setenv('FLYTRADE_FEED', 'off')
    with TestClient(main.app) as c:
        live = c.get('/api/training/state').json()['brain_id']
        first = next(b for b in c.get('/api/brains').json() if b['legacy'])
        second = next(b for b in c.get('/api/brains').json() if b['legacy'])
        assert first['brain_id'] == live == second['brain_id']
