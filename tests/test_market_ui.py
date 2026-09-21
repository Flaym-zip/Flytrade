"""Phase 1.4: restored, lively Marche page (grid + brain trading) over the
unchanged Run06/Rules06/choose06 backend. No new risk logic, no neural
learning live, no change to TRAIN/VALIDATION/TEST protections.
"""
from pathlib import Path
import pytest
from app.economy06 import Rules06, net_result
from app.orderbook import OrderBook
from app.run08 import Run08
from test_alpha06 import market, probs, advance
from test_wizard import make_client, create_brain, import_dataset, default_config, run_all_steps

ROOT = Path(__file__).resolve().parents[1]


def funded_run(tmp_path, **rule_overrides):
    r = Run08(tmp_path, market(), OrderBook())
    r.rules = Rules06(quote_mode='manual', multipliers=[3] * 3, **rule_overrides)
    r.calibrator.predict = lambda *args: probs()
    r.policy_enabled = True
    return r


# ---------------------------------------------------------------- page structure

def test_grid_script_served_and_referenced_by_the_market_page():
    assert (ROOT / 'app/static/grid.js').exists()
    html = (ROOT / 'app/static/market.html').read_text()
    assert '/static/grid.js' in html and 'id="chart"' in html
    js = (ROOT / 'app/static/market.js').read_text()
    assert 'FlyGrid.draw' in js


def test_hsb_and_brain_preference_visible_in_markup():
    html = (ROOT / 'app/static/market.html').read_text()
    assert 'brain-preference' in html
    js = (ROOT / 'app/static/market.js').read_text()
    for key in ('hausse', 'stable', 'baisse'):
        assert key in js  # LABEL map covers all three, never a fourth "wait" brain action


# ---------------------------------------------------------------- trade lifecycle vs stats

def test_trade_accepted_locks_the_chosen_cell_until_settlement(tmp_path):
    r = funded_run(tmp_path)
    x = r.start(now=132)
    fixed_row, a = x['base_row'], x['chosen']
    assert x['order_status'] == 'submitted' and a is not None
    advance(r, x['lock_due'])
    assert x['order_status'] == 'locked' and x['base_row'] == fixed_row and x['chosen'] == a
    r.close()


def test_wait_never_counts_as_a_trade(tmp_path):
    r = Run08(tmp_path, market(), OrderBook())  # policy disabled
    x = r.start(now=132)
    advance(r, x['end'] + 1)
    assert x['economic_action'] == 'attendre' and x['chosen'] is None
    assert r.stats['trades'] == 0 and r.stats['waits'] == 1
    assert r.stats['chosen'] == [0, 0, 0]  # distribution counts only real trades
    r.close()


def test_trades_wins_losses_winrate_pnl_drawdown_and_hsb_distribution_from_trades_only(tmp_path):
    r = funded_run(tmp_path)
    x = r.start(now=132)
    a = x['chosen']
    win_price = (x['base_row'] + (1, 0, -1)[a] + .3) * .5
    advance(r, x['lock_due'], price=win_price)
    advance(r, x['end'] + 1, price=win_price)
    assert r.stats['trades'] == 1 and r.stats['wins'] == 1 and r.stats['losses'] == 0
    snap = r.snapshot()
    assert snap['stats']['hit_rate'] == pytest.approx(1.0)
    expected_net = net_result(True, x['quote_lock']['effective_gross'][a], x['stake'], 0)
    assert snap['stats']['net'] == pytest.approx(expected_net)
    assert snap['stats']['capital'] == pytest.approx(20 + expected_net)
    assert snap['stats']['max_drawdown'] == pytest.approx(0)  # a win never adds drawdown
    assert snap['stats']['chosen'][a] == 1 and sum(snap['stats']['chosen']) == 1
    r.close()


def test_no_neural_learning_happens_live(tmp_path):
    r = funded_run(tmp_path)
    x = r.start(now=132)
    a = x['chosen']
    win_price = (x['base_row'] + (1, 0, -1)[a] + .3) * .5
    advance(r, x['lock_due'], price=win_price)
    advance(r, x['end'] + 1, price=win_price)
    assert r.brain.updates == 0 and r.brain.value_updates == 0
    r.close()


# ---------------------------------------------------------------- risk guardrails (unchanged logic)

def test_max_fraction_respected_by_the_adaptive_engine(tmp_path):
    r = funded_run(tmp_path, max_fraction=.05, stake_mode='adaptive')
    x = r.start(now=132)
    assert x['stake'] * (1 + r.rules.cost_per_stake) <= 20 * .05 + 1e-9
    r.close()


def test_max_stake_respected_even_with_huge_fraction(tmp_path):
    r = funded_run(tmp_path, max_fraction=1., max_stake=1., stake_mode='adaptive')
    x = r.start(now=132)
    assert x['stake'] <= 1.
    r.close()


def test_drawdown_limit_stops_new_trades(tmp_path):
    r = funded_run(tmp_path, drawdown_limit=1.)
    r.stats['peak_capital'] = 20.
    r.stats['net'] = -5.  # capital fell 5 EUR from the peak, past the 1 EUR limit
    x = r.start(now=132)
    assert x['chosen'] is None and 'perte' in x['economic_reason'].lower()
    r.close()


def test_min_edge_zero_does_not_bypass_calibration(tmp_path):
    r = Run08(tmp_path, market(), OrderBook())
    r.rules = Rules06(quote_mode='manual', multipliers=[3] * 3, min_edge=0.)
    r.policy_enabled = True  # calibrator still empty/untrained: n=0, nothing is "ready"
    x = r.start(now=132)
    assert x['chosen'] is None
    assert 'calibration' in x['economic_reason'].lower()
    r.close()


def test_position_already_open_blocks_a_second_one(tmp_path):
    r = funded_run(tmp_path)
    x1 = r.start(now=132)
    assert x1['chosen'] is not None and r.reserved > 0
    with pytest.raises(ValueError):
        r.start(now=132.5)  # same 5s slot is refused anyway, so advance a little first
    r.market.receive({'kind': 'trade', 'time': 1038, 'id': r.market.last_id + 1, 'price': 3000.2}, now=138 - 900)
    x2 = r.start(now=138 - 900)
    assert x2['chosen'] is None and 'reserv' in x2['economic_reason'].lower()
    r.close()


def test_stake_chosen_within_limits_by_the_adaptive_engine(tmp_path):
    r = funded_run(tmp_path, stake_mode='adaptive', max_stake=5.)
    x = r.start(now=132)
    from app.economy06 import STAKES
    assert x['stake'] in STAKES and x['stake'] <= 5.
    r.close()


# ---------------------------------------------------------------- settings via HTTP (reuses /api/settings)

def test_risk_settings_persist_across_restart(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        s = c.get('/api/state').json()['settings']
        s.update(min_edge=0., max_fraction=.03, max_stake=5., drawdown_limit=2.)
        r = c.post('/api/settings', json=s)
        assert r.status_code == 200
    with make_client(monkeypatch, tmp_path) as c2:
        got = c2.get('/api/state').json()['settings']
        assert got['min_edge'] == 0. and got['max_fraction'] == pytest.approx(.03)
        assert got['max_stake'] == 5. and got['drawdown_limit'] == 2.


def test_wait_reasons_endpoint_only_counts_wait_decisions(tmp_path, monkeypatch):
    r = Run08(tmp_path, market(), OrderBook())
    x = r.start(now=132)
    advance(r, x['end'] + 1)
    assert x['economic_action'] == 'attendre'
    counts = r.wait_reason_counts()
    r.close()
    assert sum(counts.values()) >= 1
    assert x['economic_reason'] in counts


# ---------------------------------------------------------------- deployment isolation (unchanged Phase 1)

def test_deploying_another_brain_does_not_disturb_the_live_one_or_its_settings(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        a = create_brain(c, 'Live-A')
        imp = import_dataset(c)
        prep = c.post(f"/api/brains/{a['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, a['workshop_id'], prep)
        c.post(f"/api/brains/{a['workshop_id']}/deploy", json={'confirm': True})
        before = c.get('/api/state').json()

        bee = create_brain(c, 'Other-B')
        prep_b = c.post(f"/api/brains/{bee['workshop_id']}/train/prepare", json=default_config(imp['dataset_id'])).json()
        run_all_steps(c, bee['workshop_id'], prep_b)
        c.post(f"/api/brains/{bee['workshop_id']}/duplicate", json={'name': 'Other-B-copy'})

        after = c.get('/api/state').json()
        assert after['brain_id'] == before['brain_id']
        assert after['weight_fingerprint'] == before['weight_fingerprint']
        assert after['settings'] == before['settings']


# ---------------------------------------------------------------- activity presets vs risk limits

def test_activity_presets_only_touch_min_edge_in_source():
    js = (ROOT / 'app/static/market.js').read_text()
    start = js.index('const PRESETS')
    block = js[start:js.index('};', start) + 1]
    for key in ('max_fraction', 'max_stake', 'drawdown_limit', 'fixed_stake'):
        assert key not in block
    assert block.count('min_edge') == 3  # prudent / normal / actif, nothing else


def test_activity_preset_leaves_risk_limits_untouched(tmp_path, monkeypatch):
    with make_client(monkeypatch, tmp_path) as c:
        s = c.get('/api/state').json()['settings']
        s.update(max_fraction=.03, max_stake=5., drawdown_limit=2., fixed_stake=5.)
        c.post('/api/settings', json=s)
        before = c.get('/api/state').json()['settings']

        # Applying an activity preset (as the UI would) must only change min_edge.
        preset_only_min_edge = dict(before, min_edge=0.)
        r = c.post('/api/settings', json=preset_only_min_edge)
        assert r.status_code == 200
        after = c.get('/api/state').json()['settings']
        assert after['min_edge'] == 0.
        assert after['max_fraction'] == before['max_fraction'] == pytest.approx(.03)
        assert after['max_stake'] == before['max_stake'] == 5.
        assert after['drawdown_limit'] == before['drawdown_limit'] == 2.
        assert after['fixed_stake'] == before['fixed_stake'] == 5.
