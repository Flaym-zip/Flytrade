"""Phase 1.3: simplified Marche page.

Brain preference (H/S/B, never WAIT) vs economic decision (TRADE/ATTENDRE)
stay separate in the journal and the API; the live brain is exactly the
named brain deployed from /brains, unaffected by training any other brain.
"""
from app.economy06 import Rules06
from app.orderbook import OrderBook
from app.run08 import Run08
from test_alpha06 import market, probs, advance
from test_wizard import make_client, create_brain, import_dataset, default_config, run_all_steps


def funded_run(tmp_path):
    r = Run08(tmp_path, market(), OrderBook())
    r.rules = Rules06(quote_mode='manual', multipliers=[3] * 3)
    r.calibrator.predict = lambda *args: probs()
    r.policy_enabled = True
    return r


# ---------------------------------------------------------------- brain preference vs economy

def test_neural_preference_always_hsb_never_wait_even_when_economy_waits(tmp_path):
    r = Run08(tmp_path, market(), OrderBook())  # policy disabled: economy will wait
    x = r.start(now=132)
    assert x['brain_preferred_action'] in ('hausse', 'stable', 'baisse')
    assert x['economic_action'] == 'attendre'
    r.close()


def test_economic_action_separate_field_from_brain_preference(tmp_path):
    r = funded_run(tmp_path)
    x = r.start(now=132)
    assert x['chosen'] is not None
    assert x['economic_action'] in ('hausse', 'stable', 'baisse')
    assert x['brain_preferred_action'] in ('hausse', 'stable', 'baisse')
    # Both fields exist independently; the record never conflates them under one key.
    assert 'brain_preferred_action' in x and 'economic_action' in x
    r.close()


def test_wait_comes_only_from_economic_engine_not_the_brain(tmp_path):
    r = Run08(tmp_path, market(), OrderBook())
    x = r.start(now=132)
    assert x['chosen'] is None and x['economic_action'] == 'attendre'
    assert x['brain_preferred_action'] != 'attendre'  # the brain still picked H/S/B
    r.close()


def test_wait_reason_preserved(tmp_path):
    r = Run08(tmp_path, market(), OrderBook())
    x = r.start(now=132)
    assert x['economic_action'] == 'attendre'
    assert isinstance(x['economic_reason'], str) and len(x['economic_reason']) > 0
    r.close()


def test_tie_break_preserved_on_a_fresh_untrained_brain(tmp_path):
    # An untrained Brain06 has uniform .5 weights: every candidate scores identically
    # (q == 0 for all three), so the very first decision is a deterministic 3-way tie.
    r = Run08(tmp_path, market(), OrderBook())
    x = r.start(now=132)
    assert x['brain_tie_break']['used'] is True
    assert set(x['brain_tie_break']['candidates']) == {'hausse', 'stable', 'baisse'}
    assert x['brain_tie_break']['selected'] == x['brain_preferred_action']
    r.close()


def test_history_separates_brain_and_economic_decisions(tmp_path):
    r = funded_run(tmp_path)
    x1 = r.start(now=132)
    advance(r, x1['end'] + 1)  # settle the trade window into the opportunities table
    r.policy_enabled = False
    x2 = r.start(now=x1['end'] + 2 - 900)
    advance(r, x2['end'] + 1)  # settle the wait window too
    recs = r.export_rows()
    r.close()
    assert len(recs) == 2
    trade = next(x for x in recs if x['economic_action'] != 'attendre')
    wait = next(x for x in recs if x['economic_action'] == 'attendre')
    assert trade['economic_action'] in ('hausse', 'stable', 'baisse')
    assert wait['economic_action'] == 'attendre'
    assert trade['brain_preferred_action'] in ('hausse', 'stable', 'baisse')
    assert wait['brain_preferred_action'] in ('hausse', 'stable', 'baisse')


# ---------------------------------------------------------------- deployed brain identity (HTTP)

def test_deployed_brain_identified_correctly_on_market(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Fly-Kraken-2100-A')
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, b['workshop_id'], prep)
        trained = c.get('/api/brains/' + b['workshop_id']).json()
        c.post(f"/api/brains/{b['workshop_id']}/deploy", json={'confirm': True})
        state = c.get('/api/state').json()
        assert state['brain_id'] == trained['brain_id']
        assert state['weight_fingerprint'] == trained['weight_fingerprint']
        brains = c.get('/api/brains').json()
        deployed = next(x for x in brains if x['workshop_id'] == b['workshop_id'])
        assert deployed['state'] == 'deploye'


def test_live_weight_fingerprint_matches_deployed_weights_exactly(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Fly-B')
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        snap = run_all_steps(c, b['workshop_id'], prep)
        before_deploy_fingerprint = snap['weight_fingerprint']
        c.post(f"/api/brains/{b['workshop_id']}/deploy", json={'confirm': True})
        state = c.get('/api/state').json()
        assert state['weight_fingerprint'] == before_deploy_fingerprint


def test_training_another_brain_never_silently_changes_the_live_brain(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        a = create_brain(c, 'A-deployed')
        imp = import_dataset(c)
        prep_a = c.post(f"/api/brains/{a['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, a['workshop_id'], prep_a)
        c.post(f"/api/brains/{a['workshop_id']}/deploy", json={'confirm': True})
        live_before = c.get('/api/state').json()

        bee = create_brain(c, 'B-other')
        prep_b = c.post(f"/api/brains/{bee['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, bee['workshop_id'], prep_b)

        live_after = c.get('/api/state').json()
        assert live_after['brain_id'] == live_before['brain_id']
        assert live_after['weight_fingerprint'] == live_before['weight_fingerprint']


def test_deploy_visible_immediately_on_market_without_extra_action(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Immediate')
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, b['workshop_id'], prep)
        trained = c.get('/api/brains/' + b['workshop_id']).json()
        c.post(f"/api/brains/{b['workshop_id']}/deploy", json={'confirm': True})
        # No further action (no reload/reset): the very next read already reflects it.
        assert c.get('/api/state').json()['brain_id'] == trained['brain_id']


def test_restart_keeps_the_correct_live_brain(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        b = create_brain(c, 'Persistent')
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{b['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, b['workshop_id'], prep)
        trained = c.get('/api/brains/' + b['workshop_id']).json()
        c.post(f"/api/brains/{b['workshop_id']}/deploy", json={'confirm': True})
    with make_client(monkeypatch, tmp_path) as c2:
        state = c2.get('/api/state').json()
        assert state['brain_id'] == trained['brain_id']
        assert state['weight_fingerprint'] == trained['weight_fingerprint']


def test_market_page_and_routes_serve(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        assert c.get('/').status_code == 200
        assert c.get('/training').status_code == 200  # old technical page, still reachable
