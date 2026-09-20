"""Phase 0 regression tests: experimental-integrity invariants.

Brain identity, checkpoint/schema migration, calibration validation, dataset
identity/versioning, TEST protection, archive redaction, chronological split,
H/S/B classification exclusions, and metrics correctness.
"""
import copy, json, math
import numpy as np
import pytest
from app.academy06 import Academy06, Config06, validate_row
from app.academy08 import Academy08
from app.brain06 import Brain06
from app.dataset import Episode, split_chronological
from app.economics import Calibrator
from app.live import GridObservation, row_of
from app.metrics import classification_metrics, stats_from_confusion


def obs():
    return GridObservation(tuple(3000+np.linspace(0, .8, 31)), .5/3000*100, 18, .4, 3000.2)


def mkrow(i, source='src', prices=None, placed_base=10000, spacing=60, base_row=200, reference=100.2):
    placed = placed_base+i*spacing; start = placed+15
    if prices is None:
        prices = [reference+(1, 0, -1)[i % 3]*.5]
    ticks = [[start+1+j*.1, p, i*1000+j] for j, p in enumerate(prices)]
    touches = [any(row_of(p) == base_row+s for p in prices) for s in (1, 0, -1)]
    return dict(schema06=1, id=i, status='observed', source=source, placed=placed, start=start,
                end=start+5, history_end=placed, history=[reference]*31, reference=reference,
                base_row=base_row, chosen=None, touches=touches, window_ticks=ticks,
                book=None, quote_lock=None)


def lines(n=90, source='src'):
    return (json.dumps(mkrow(i, source=source)) for i in range(n))


CFG = Config06(n_kc=1024, epochs=1, mode='chronological', use_liquidity=False)


# ---------------------------------------------------------------- 1. brain identity

def test_two_brains_same_params_have_different_brain_id_but_same_weights():
    a = Brain06(seed=7, n_kc=1024, sparsity=.05, use_liquidity=False)
    b = Brain06(seed=7, n_kc=1024, sparsity=.05, use_liquidity=False)
    assert a.brain_id != b.brain_id
    assert a.fingerprint() == b.fingerprint()


def test_brain_id_stable_after_learning_weight_fingerprint_changes():
    b = Brain06(seed=3, n_kc=1024)
    bid = b.brain_id; fp0 = b.fingerprint()
    d = b.predict(obs())
    b.learn_outcomes(d, (True, False, False), .5, 'all')
    assert b.brain_id == bid
    assert b.fingerprint() != fp0


def test_brain_id_and_fingerprint_are_not_interchangeable():
    b = Brain06(seed=1, n_kc=1024)
    assert b.brain_id != b.fingerprint()
    d = b.to_dict()
    assert d['brain_id'] != Brain06.from_dict(d).fingerprint()


# ---------------------------------------------------------------- 2. checkpoints / migration

def test_current_schema_accepted_and_roundtrips():
    b = Brain06(seed=2, n_kc=1024)
    d = b.to_dict()
    assert d['schema'] == Brain06.SCHEMA
    r = Brain06.from_dict(d)
    assert r.brain_id == b.brain_id and r.fingerprint() == b.fingerprint()


def test_unknown_schema_refused():
    d = Brain06(n_kc=1024).to_dict(); d['schema'] = 99
    with pytest.raises(ValueError):
        Brain06.from_dict(d)


def test_incompatible_kind_refused():
    d = Brain06(n_kc=1024).to_dict(); d['kind'] = 'something-else'
    with pytest.raises(ValueError):
        Brain06.from_dict(d)


def test_incompatible_encoder_refused():
    d = Brain06(n_kc=1024).to_dict(); d['encoder'] = 'other-encoder-v1'
    with pytest.raises(ValueError):
        Brain06.from_dict(d)


def test_schema1_migrates_only_via_explicit_import_legacy():
    b = Brain06(n_kc=1024)
    legacy = b.to_dict(); del legacy['brain_id']; legacy['schema'] = 1
    with pytest.raises(ValueError):
        Brain06.from_dict(legacy)  # no silent conversion
    migrated = Brain06.import_legacy(legacy, 'some-stable-key')
    assert migrated.fingerprint() == b.fingerprint()
    again = Brain06.import_legacy(legacy, 'some-stable-key')
    assert again.brain_id == migrated.brain_id  # same key -> same identity, reproducible


def test_import_legacy_refuses_non_schema1():
    d = Brain06(n_kc=1024).to_dict()
    with pytest.raises(ValueError):
        Brain06.import_legacy(d, 'key')


# ---------------------------------------------------------------- 3. calibration

def valid_calibration_dict(n=40, fingerprint='a'*64):
    return dict(fingerprint=fingerprint, min_total=30, min_bin=10, n=n,
                actions=[[{'min': 0., 'max': 1., 'n': n, 'hits': n//2}] for _ in range(3)])


def test_calibration_group_with_zero_n_when_it_should_not_exist_refused():
    d = valid_calibration_dict(); d['actions'][0][0]['n'] = 0
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


def test_calibration_hits_greater_than_n_refused():
    d = valid_calibration_dict(); d['actions'][0][0]['hits'] = d['actions'][0][0]['n']+1
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


def test_calibration_negative_hits_refused():
    d = valid_calibration_dict(); d['actions'][0][0]['hits'] = -1
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


def test_calibration_invalid_bounds_refused():
    d = valid_calibration_dict(); d['actions'][0][0]['min'] = 1.; d['actions'][0][0]['max'] = 0.
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


@pytest.mark.parametrize('bad', [float('nan'), float('inf')])
def test_calibration_nan_inf_bounds_refused(bad):
    d = valid_calibration_dict(); d['actions'][0][0]['max'] = bad
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


def test_calibration_bad_fingerprint_refused():
    d = valid_calibration_dict(); d['fingerprint'] = 'not-hex-and-wrong-length'
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


def test_calibration_wrong_action_count_refused():
    d = valid_calibration_dict(); d['actions'] = d['actions'][:2]
    with pytest.raises(ValueError):
        Calibrator.from_dict(d)


def test_calibration_empty_is_safe_and_predict_never_zerodivisionerrors():
    c = Calibrator()
    out = c.predict([0., 0., 0.], 'anyfingerprint')
    assert all(o['ready'] is False and o['p'] is None for o in out)


def test_calibration_roundtrip_predict_no_zerodivisionerror():
    b = Brain06(n_kc=1024)
    d = b.predict(obs())
    c = Calibrator(min_total=1, min_bin=1)
    c.fit([d.scores.tolist()]*5, [[True, False, False]]*5, b.fingerprint())
    restored = Calibrator.from_dict(c.to_dict())
    restored.predict(d.scores.tolist(), b.fingerprint())  # must not raise


# ---------------------------------------------------------------- 4. datasets

def test_two_distinct_kraken_imports_get_distinct_dataset_ids_and_are_not_merged(tmp_path):
    a = Academy06(tmp_path)
    r1 = a.import_lines(lines(20, source='kraken'), name='kraken-import-1')
    r2 = a.import_lines((json.dumps(mkrow(i, source='kraken', placed_base=99000)) for i in range(20)),
                         name='kraken-import-2')
    assert r1['dataset_id'] and r2['dataset_id'] and r1['dataset_id'] != r2['dataset_id']
    ds = {d['dataset_id']: d for d in a.datasets()}
    assert ds[r1['dataset_id']]['windows'] == 20 and ds[r2['dataset_id']]['windows'] == 20
    a.close()


def test_reimporting_same_windows_keeps_original_dataset_membership(tmp_path):
    a = Academy06(tmp_path)
    r1 = a.import_lines(lines(20, source='kraken'), name='first')
    r2 = a.import_lines(lines(20, source='kraken'), name='second-attempt')
    ids1 = {w[0] for w in a.db.execute('SELECT uid FROM dataset_windows WHERE dataset_id=?', (r1['dataset_id'],))}
    assert r2['added'] == 0  # pure duplicates, nothing new on disk
    assert len(ids1) == 20
    a.close()


def test_voluntary_merge_creates_new_dataset_id(tmp_path):
    a = Academy06(tmp_path)
    r1 = a.import_lines(lines(20, source='kraken'))
    r2 = a.import_lines((json.dumps(mkrow(i, source='kraken', placed_base=99000)) for i in range(20)))
    merged = a.create_dataset_version([r1['dataset_id'], r2['dataset_id']], 'merged-set')
    assert merged['dataset_id'] not in (r1['dataset_id'], r2['dataset_id'])
    assert merged['windows'] == 40
    a.close()


def test_merge_of_different_sources_refused(tmp_path):
    a = Academy06(tmp_path)
    r1 = a.import_lines(lines(20, source='kraken-eth'))
    r2 = a.import_lines(lines(20, source='kraken-btc'))
    with pytest.raises(ValueError):
        a.create_dataset_version([r1['dataset_id'], r2['dataset_id']], 'bad-merge')
    a.close()


def test_source_and_hash_conserved_and_stats_track_multiple_and_aucune(tmp_path):
    a = Academy06(tmp_path)
    rows_ = [mkrow(i, source='kraken') for i in range(18)]
    rows_.append(mkrow(18, source='kraken', prices=[110.]))  # far away -> aucune
    rows_.append(mkrow(19, source='kraken', prices=[100.7, 99.7]))  # two bands -> multiple
    r = a.import_lines(json.dumps(x) for x in rows_)
    ds = next(d for d in a.datasets() if d['dataset_id'] == r['dataset_id'])
    assert ds['source'] == 'kraken' and ds['content_hash'] == r['sha256']
    assert ds['stats']['aucune'] == 1 and ds['stats']['multiple'] == 1
    a.close()


# ---------------------------------------------------------------- 5. TEST protection

def finish(a):
    while a.position < len(a.plan):
        a.step_batch(40)


def test_exposed_test_stays_marked_and_cannot_be_reopened(tmp_path):
    a = Academy08(tmp_path); a.import_lines(lines(100))
    a.create(CFG); finish(a); a.open_test()
    assert a.test_opened
    with pytest.raises(ValueError):
        a.open_test()


def test_continuing_training_does_not_reopen_test(tmp_path):
    a = Academy08(tmp_path); a.import_lines(lines(100))
    a.create(CFG); finish(a); a.open_test(); finish(a)
    a.continue_training(1)
    # Exposure persists across continue: test_opened stays true (already burned),
    # and a further explicit open_test is refused rather than granting a fresh look.
    assert a.test_opened
    with pytest.raises(ValueError):
        a.open_test()
    assert a.preview(a.config)['reused_test'] == len(a.splits['test'])


def test_replaying_does_not_reopen_test(tmp_path):
    a = Academy08(tmp_path); a.import_lines(lines(100))
    a.create(CFG); finish(a); a.open_test(); uids = list(a.splits['test'])
    a.replay()
    assert a.test_opened  # replay reuses the same split; its TEST stays marked exposed
    with pytest.raises(ValueError):
        a.open_test()
    assert a._seen(uids) == len(uids)


def test_recreating_protocol_from_scratch_does_not_forget_exposure(tmp_path):
    a = Academy08(tmp_path); a.import_lines(lines(100))
    a.create(CFG); finish(a); a.open_test(); uids = list(a.splits['test'])
    a.reset_training(CFG); a.create(CFG)
    assert a._seen(a.splits['test']) or a._seen(uids)


def test_exposed_test_examples_do_not_enter_train_by_default(tmp_path):
    a = Academy08(tmp_path)
    base = a.import_lines(lines(100, source='kraken'))
    a.create(CFG.model_copy(update={'dataset_id': base['dataset_id']})); finish(a); a.open_test()
    # New data placed entirely LATER shifts the old TEST rows earlier in the merged
    # chronological order, so they would now fall inside TRAIN unless guarded.
    later = a.import_lines((json.dumps(mkrow(i, source='kraken', placed_base=50000)) for i in range(400)))
    merged = a.create_dataset_version([base['dataset_id'], later['dataset_id']], 'combined')
    cfg = CFG.model_copy(update={'dataset_id': merged['dataset_id']})
    with pytest.raises(ValueError):
        a.create(cfg)
    # Explicit, marked reuse is the only way to proceed.
    allowed = a.create_dataset_version([base['dataset_id'], later['dataset_id']], 'combined-allowed',
                                        allow_exposed_training=True)
    assert allowed['allow_exposed_training'] is True
    cfg2 = CFG.model_copy(update={'dataset_id': allowed['dataset_id']})
    preview = a.preview(cfg2)
    assert preview['split']['benchmark_contaminated'] is True


# ---------------------------------------------------------------- 6. archives

def test_archive_hides_test_before_exposure_and_reveals_after(tmp_path):
    a = Academy08(tmp_path); a.import_lines(lines(100))
    a.create(CFG); session_a = a.session; finish(a)
    a.create(CFG)  # transition away from A archives it, still unopened
    archived = a.archive(session_a)
    assert archived['splits']['test'] == [] and archived['test_redacted'] is True
    assert archived['split_report']['splits']['test'].get('hidden') is True
    assert 'Test gele' not in archived.get('metrics', {})
    # Session B shares the same corpus/config/seed, so the same TEST uids.
    assert a.splits['test'] == json.loads(
        a.db.execute('SELECT payload FROM archives WHERE session=?', (session_a,)).fetchone()[0]
    )['splits']['test']
    finish(a); a.open_test(); finish(a)
    revealed = a.archive(session_a)
    assert revealed['splits']['test'] and 'test_redacted' not in revealed


# ---------------------------------------------------------------- 7. split

def test_split_ratios_70_15_15_chronological_purge_and_train_only_shuffle():
    eps = [Episode.from_dict(Episode(str(i), 'src', 1000.+i*60, 1015.+i*60, 1020.+i*60, 100.2, 200,
                                      1000.+i*60, tuple([100.2]*31), (), (True, False, False)).to_dict())
           for i in range(100)]
    splits, report = split_chronological(eps)
    total = sum(len(v) for v in splits.values())
    assert total == 100 - report['purged']
    assert [e.uid for e in splits['train']] == sorted([e.uid for e in splits['train']], key=lambda u: int(u))
    assert [e.uid for e in splits['validation']] == sorted([e.uid for e in splits['validation']], key=lambda u: int(u))
    assert [e.uid for e in splits['test']] == sorted([e.uid for e in splits['test']], key=lambda u: int(u))
    val_cut = min(e.support_start for e in splits['validation'])
    test_cut = min(e.support_start for e in splits['test'])
    assert all(e.end+5 <= val_cut for e in splits['train'])
    assert all(e.end+5 <= test_cut for e in splits['validation'])


def test_split_no_leakage_between_partitions_after_purge(tmp_path):
    a = Academy08(tmp_path); a.import_lines(lines(120))
    a.create(CFG)
    train, val, test = set(a.splits['train']), set(a.splits['validation']), set(a.splits['test'])
    assert not (train & val) and not (val & test) and not (train & test)


def test_shuffle_only_applies_to_train_validation_and_test_remain_chronological(tmp_path):
    a = Academy08(tmp_path)
    rows_ = [mkrow(i) for i in range(120)]
    placed_of = {validate_row(r)[0].uid: r['placed'] for r in rows_}
    a.import_lines(json.dumps(r) for r in rows_)
    cfg = CFG.model_copy(update={'shuffle_train': True})
    a.create(cfg)
    train_ids_in_plan = [uid for phase, uid in a.plan if phase.startswith('Train')]
    assert set(train_ids_in_plan) == set(a.splits['train'])
    assert train_ids_in_plan != a.splits['train']  # shuffled
    assert a.splits['validation'] == sorted(a.splits['validation'], key=placed_of.get)
    assert a.splits['test'] == sorted(a.splits['test'], key=placed_of.get)


# ---------------------------------------------------------------- 8. classification exclusions

def test_multi_touch_and_no_touch_excluded_from_hsb_training_set_but_kept_in_dataset_stats(tmp_path):
    a = Academy08(tmp_path)
    rows_ = [mkrow(i) for i in range(28)]
    rows_.append(mkrow(28, prices=[110.]))          # aucune
    rows_.append(mkrow(29, prices=[100.7, 99.7]))   # multiple
    r = a.import_lines(json.dumps(x) for x in rows_)
    ds = next(d for d in a.datasets() if d['dataset_id'] == r['dataset_id'])
    assert ds['stats']['aucune'] == 1 and ds['stats']['multiple'] == 1
    a.create(CFG)
    used_uids = {uid for _, uid in a.plan}
    all_uids = {w[0] for w in a.db.execute('SELECT uid FROM dataset_windows WHERE dataset_id=?', (r['dataset_id'],))}
    assert len(all_uids) == 30 and len(used_uids) < 30


# ---------------------------------------------------------------- 9. metrics

def test_confusion_matrix_and_recalls_hand_computed():
    actual =    [0, 0, 0, 1, 1, 2, 2, 2, 2]
    predicted = [0, 0, 1, 1, 0, 2, 2, 1, 0]
    m = classification_metrics(actual, predicted)
    assert m['confusion_matrix'] == [[2, 1, 0], [1, 1, 0], [1, 1, 2]]
    assert m['support'] == [3, 2, 4]
    assert m['recall'] == [2/3, 1/2, 2/4]
    assert m['accuracy'] == pytest.approx(5/9)
    assert m['balanced_accuracy'] == pytest.approx((2/3+1/2+2/4)/3)


def test_balanced_accuracy_averages_only_present_classes():
    m = classification_metrics([0, 0, 1, 1], [0, 1, 1, 1])
    assert m['support'] == [2, 2, 0]
    assert m['recall'][2] is None
    assert m['balanced_accuracy'] == pytest.approx((0.5+1.0)/2)


def test_metrics_rejects_mismatched_lengths_and_bad_classes():
    with pytest.raises(ValueError):
        classification_metrics([0, 1], [0])
    with pytest.raises(ValueError):
        classification_metrics([0, 3], [0, 1])


def test_stats_from_confusion_matches_classification_metrics():
    actual, predicted = [0, 1, 2, 2, 1, 0], [0, 1, 2, 1, 1, 0]
    matrix = [[0, 0, 0] for _ in range(3)]
    for y, p in zip(actual, predicted):
        matrix[y][p] += 1
    assert stats_from_confusion(matrix) == classification_metrics(actual, predicted)


def test_step_batch_metrics_use_shared_metrics_module(tmp_path):
    a = Academy06(tmp_path); a.import_lines(lines(100))
    a.create(CFG); a.step_batch(20)
    phase = next(p for p in a.metrics if a.metrics[p]['n'])
    stats = stats_from_confusion(a.metrics[phase]['confusion_matrix'])
    assert a.metrics[phase]['recall'] == stats['recall']
    assert a.metrics[phase]['balanced_accuracy'] == stats['balanced_accuracy']


# ---------------------------------------------------------------- 10. economics separated from brain

def test_economic_head_config_is_locked_false():
    with pytest.raises(Exception):
        Config06(economic_head=True)
    assert Config06().economic_head is False


def test_no_value_learning_during_scientific_training(tmp_path):
    a = Academy06(tmp_path); a.import_lines(lines(100))
    a.create(CFG)
    while a.position < len(a.plan):
        a.step_batch(20)
    assert a.brain.value_updates == 0
