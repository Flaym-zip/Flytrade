import numpy as np
import pytest
from app.brain import MushroomBody, N_ACTIVE, Observation
from app.engine import Scenario


def scenario(**kwargs):
    args = dict(history_returns=[.12,.10,.08,.14,.10,.06],
                future_returns=[.08,.06,.10,.12,.08,.10], epsilon=0)
    args.update(kwargs)
    return Scenario(**args)


def test_sparse_positive_activity():
    model = MushroomBody()
    decision = model.decide(scenario().observation(), 0)
    assert np.count_nonzero(decision.kc) == N_ACTIVE
    assert np.all((decision.kc == 0) | (decision.kc == 1))
    assert np.all(decision.mbon_plus >= 0)
    assert np.all(decision.mbon_minus >= 0)


def test_no_future_or_scenario_name_in_decision():
    a, b = MushroomBody(5), MushroomBody(5)
    sa = scenario(name="hausse annoncee", future_returns=[.1]*6)
    sb = scenario(name="baisse annoncee", future_returns=[-.1]*6)
    da, db = a.decide(sa.observation()), b.decide(sb.observation())
    assert da.action == db.action
    np.testing.assert_array_equal(da.kc, db.kc)
    np.testing.assert_array_equal(da.scores, db.scores)
    assert sa.observation() == sb.observation()


def test_price_scale_does_not_leak_identity():
    model = MushroomBody()
    a, b = scenario(price=100), scenario(price=10000)
    np.testing.assert_allclose(model.features(a.observation()), model.features(b.observation()), atol=1e-12)
    np.testing.assert_array_equal(model.encode(a.observation())[0], model.encode(b.observation())[0])


def test_animation_time_does_not_affect_learning():
    assert scenario(duration=1).observation() == scenario(duration=60).observation()


@pytest.mark.parametrize('reward', [-1.0, 1.0])
def test_plasticity_is_action_and_eligibility_gated(reward):
    model = MushroomBody()
    d = model.decide(scenario().observation(), 0)
    before_p, before_m = model.w_plus.copy(), model.w_minus.copy()
    result = model.reinforce(d, reward, .25)
    for a in set(range(3)) - {d.action}:
        np.testing.assert_array_equal(model.w_plus[:, a], before_p[:, a])
        np.testing.assert_array_equal(model.w_minus[:, a], before_m[:, a])
    np.testing.assert_array_equal(model.w_plus[d.kc == 0], before_p[d.kc == 0])
    assert np.sign(result['post_scores'][d.action]) == np.sign(reward)
    assert result['dan_plus'] >= 0 and result['dan_minus'] >= 0
    assert result['dan_plus'] * result['dan_minus'] == 0


def test_frozen_plasticity():
    model = MushroomBody()
    d = model.decide(scenario().observation(), 0)
    before = model.to_dict()
    result = model.reinforce(d, -1, .25, enabled=False)
    assert model.to_dict() == before
    assert result['weight_change'] == 0
    assert result['dan_minus'] > 0


def test_toy_repeated_association_and_reversal():
    model = MushroomBody()
    obs = scenario().observation()
    chosen = []
    for _ in range(30):
        d = model.decide(obs, 0)
        chosen.append(d.action)
        model.reinforce(d, 1 if d.action == 0 else -1, .25)
    assert chosen[-10:] == [0]*10
    after = []
    for _ in range(30):
        d = model.decide(obs, 0)
        after.append(d.action)
        model.reinforce(d, 1 if d.action == 2 else -1, .25)
    assert after[-10:] == [2]*10
    assert np.all(model.w_plus >= 0) and np.all(model.w_plus <= 1)
    assert np.all(model.w_minus >= 0) and np.all(model.w_minus <= 1)


def test_checkpoint_reproduces_rng_and_weights():
    model = MushroomBody(12)
    d = model.decide(scenario().observation(), .2)
    model.reinforce(d, 1, .3)
    restored = MushroomBody.from_dict(model.to_dict())
    for _ in range(20):
        a, b = model.decide(scenario().observation(), .3), restored.decide(scenario().observation(), .3)
        assert a.action == b.action
        np.testing.assert_array_equal(a.scores, b.scores)


def test_distinct_contexts_can_activate_distinct_kc():
    model = MushroomBody()
    up = model.encode(scenario().observation())[0]
    down = model.encode(scenario(history_returns=[-.1]*6).observation())[0]
    assert np.count_nonzero(up != down) > 10


@pytest.mark.parametrize('history', [(1,2,3), (1,0,2,3), (1,2,float('nan'),4)])
def test_invalid_observations(history):
    with pytest.raises(ValueError):
        MushroomBody().decide(Observation(history, .15, 6))
