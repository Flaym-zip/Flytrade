"""Local replay experiments with independent, paired learners.

The environment owns outcomes. Learners see only immutable causal observations,
then the scalar reward of THEIR own choice. Validation/test never reinforce.
All authoritative state and completed records are committed together in SQLite.
"""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .brain import ACTIONS, Decision
from .compartment_brain import CircuitSettings
from .live import GridBrain, GridObservation
from .motif_brain import MotifBrain
from .dataset import Episode, parse_jsonl, distribution, split_chronological, curriculum, sha, canonical
from .engine import stamp


class Protocol(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2**32-1)
    epochs: int = Field(default=5, ge=1, le=30)
    epsilon: float = Field(default=.15, ge=0, le=1)
    learning_rate: float = Field(default=.25, gt=0, le=1)
    mode: Literal['curriculum', 'chronological'] = 'curriculum'
    apl: StrictBool = True
    feedback: StrictBool = True
    reversal: StrictBool = True
    temporal: StrictBool = True
    compartments: StrictBool = True
    plasticity: StrictBool = True
    credit_delay: float = Field(default=0, ge=0, le=240)
    delay: float = Field(default=.5, ge=.1, le=5)

    def circuit(self):
        return CircuitSettings(apl=self.apl, feedback=self.feedback,
                               temporal=self.temporal, compartments=self.compartments)


class LinearBandit:
    """Small normalized LMS bandit on the same 20 candidate features as MotifBrain.

    Only its chosen action receives feedback. No supervised access to all labels.
    """
    def __init__(self, seed=42):
        self.seed = seed; self.rng = np.random.default_rng(seed)
        self.weights = np.zeros((3, 21)); self.updates = 0

    def decide(self, obs, epsilon, config):
        context = MotifBrain.context(obs)
        if not config.temporal: context[[0, 1, 2, 3, 4, 5, 6, 9, 10, 15]] = 0
        features = []
        for a in range(3):
            indicator = np.full(3, -1.); indicator[a] = 1
            features.append(np.r_[context, indicator,
                          np.tanh((1, 0, -1)[a]+.5-obs.row_fraction), 1.])
        features = np.array(features)
        q = np.clip(np.einsum('af,af->a', features, self.weights), -1, 1)
        exploratory = bool(self.rng.random() < epsilon)
        a = int(self.rng.integers(3)) if exploratory else int(self.rng.choice(
            np.flatnonzero(np.isclose(q, q.max(), atol=1e-12, rtol=0))))
        return Decision(a, exploratory, np.array([]), (q+1)/2, (1-q)/2, q,
                        features[a], {'all_features': features.tolist()})

    def reinforce(self, d, reward, rate, enabled=True, **kwargs):
        if enabled:
            self.weights[d.action] += rate*(reward-d.scores[d.action])*d.features/max(1., d.features@d.features)
            self.updates += 1
        return {'updated': enabled, 'reward': reward}

    def to_dict(self):
        return {'kind': 'linear-bandit', 'seed': self.seed, 'updates': self.updates,
                'weights': self.weights.tolist(), 'rng': copy.deepcopy(self.rng.bit_generator.state)}

    @classmethod
    def from_dict(cls, x):
        b = cls(x['seed']); b.weights = np.array(x['weights']); b.updates = x['updates']
        b.rng.bit_generator.state = x['rng']; return b


def restore_agents(data):
    return {'motifs': MotifBrain.from_dict(data['motifs']),
            'reference': GridBrain.from_dict(data['reference']),
            'linear': LinearBandit.from_dict(data['linear'])}


def new_agents(seed):
    return {'motifs': MotifBrain(seed), 'reference': GridBrain(seed), 'linear': LinearBandit(seed)}


def stats_blank():
    return {'n': 0, 'agents': {a: {'wins': 0, 'points': 0, 'choices': [0]*3,
              'wins_chosen': [0]*3, 'kc_counts': [], 'kc_union': [], 'score_rows': [],
              'active_overlaps': []} for a in ('motifs', 'reference', 'linear')},
            'always': [0]*3, 'random_expected_wins': 0., 'oracle_wins': 0,
            'exclusive_n': [0]*3, 'exclusive_correct': {a: [0]*3 for a in ('motifs', 'reference', 'linear')}}


def wilson(w, n):
    if n == 0: return None
    p = w/n; z = 1.959963984540054
    den = 1+z*z/n
    mid = (p+z*z/(2*n))/den
    spread = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [mid-spread, mid+spread]  # Descriptive only: trials are not IID.


def auc(scores, labels):
    pos = np.array(scores)[np.array(labels, dtype=bool)]
    neg = np.array(scores)[~np.array(labels, dtype=bool)]
    if not len(pos) or not len(neg): return None
    # O(n log n) rank statistic, avoiding a quadratic allocation.
    neg = np.sort(neg)
    left = np.searchsorted(neg, pos, side='left'); right = np.searchsorted(neg, pos, side='right')
    return float((left+.5*(right-left)).sum()/(len(pos)*len(neg)))


class Training:
    def __init__(self, data_dir: Path):
        self.db = sqlite3.connect(data_dir/'flytrade-training.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS datasets (fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (session TEXT, step INTEGER, payload TEXT, PRIMARY KEY(session,step));
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        self.episodes = []; self.report = None; self.import_report = None
        self.agents = new_agents(42); self.config = Protocol()
        self.plan = []; self.position = 0; self.session = None; self.metrics = {}
        self.logs = []; self.last = None; self.pending = None; self.auto = False
        self.next_at = 0.; self.fatal = None; self.eval_used = 0; self.kind = 'idle'
        self.frozen_agents = None; self.origin_dataset = None
        raw = self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if raw:
            self._restore(json.loads(raw[0])); self.log('Session restauree, execution automatique arretee.')
        else: self._save()

    def close(self):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)'); self.db.close()

    def log(self, text):
        self.logs.append({'time': stamp(), 'message': text}); self.logs = self.logs[-60:]

    def state_dict(self):
        return copy.deepcopy({'schema': 1, 'agents': {k: v.to_dict() for k, v in self.agents.items()},
            'dataset': self.report['fingerprint'] if self.report else None,
            'import_report': self.import_report, 'config': self.config.model_dump(),
            'plan': self.plan, 'position': self.position, 'session': self.session,
            'metrics': self.metrics, 'last': self.last, 'logs': self.logs,
            'eval_used': self.eval_used, 'kind': self.kind,
            'frozen_agents': self.frozen_agents, 'origin_dataset': self.origin_dataset})

    def _restore(self, raw):
        if raw.get('schema') != 1: raise ValueError('Memoire apprentissage incompatible.')
        self.agents = restore_agents(raw['agents']); self.config = Protocol(**raw['config'])
        for key in ('plan', 'position', 'session', 'metrics', 'last', 'logs', 'eval_used', 'kind', 'frozen_agents', 'origin_dataset'):
            setattr(self, key, raw.get(key))
        self.import_report = raw.get('import_report')
        if raw.get('dataset'):
            row = self.db.execute('SELECT payload FROM datasets WHERE fingerprint=?', (raw['dataset'],)).fetchone()
            data = json.loads(row[0]); self.episodes = [Episode.from_dict(e) for e in data['episodes']]
            self.report = data['report']
        else: self.episodes = []; self.report = None

    def _save(self, record=None, dataset=None):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            if dataset:
                self.db.execute('INSERT OR IGNORE INTO datasets VALUES (?,?)',
                                (self.report['fingerprint'], canonical(dataset)))
            self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (canonical(self.state_dict()),))
            if self.session:
                meta = {'id': self.session, 'config': self.config.model_dump(), 'kind': self.kind,
                        'dataset': self.report['fingerprint'] if self.report else None,
                        'position': self.position, 'total': len(self.plan),
                        'metrics': self.summary_metrics(), 'timestamp': stamp()}
                self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (self.session, canonical(meta)))
            if record:
                self.db.execute('INSERT INTO records VALUES (?,?,?)',
                                (self.session, self.position, canonical(record)))
            self.db.commit()
        except Exception:
            self.db.rollback(); raise

    def idle_required(self):
        if self.pending or self.auto: raise ValueError('Mettre en pause et terminer le choix en cours.')
        if self.fatal: raise ValueError(self.fatal)

    def import_data(self, raw: bytes):
        self.idle_required()
        episodes, imported = parse_jsonl(raw)
        _, report = split_chronological(episodes)
        before = self.state_dict()
        self.episodes, self.report, self.import_report = episodes, report, imported
        self.plan = []; self.position = 0; self.session = None; self.metrics = {}
        self.last = None; self.kind = 'dataset'; self.eval_used = 0; self.frozen_agents = None
        self.origin_dataset = None
        self.log(f"Import : {len(episodes)} fenetres valides ; {report['purged']} ecartees aux frontieres.")
        try: self._save(dataset={'episodes': [e.to_dict() for e in episodes], 'report': report})
        except Exception: self._restore(before); raise

    def get_splits(self):
        if not self.report: raise ValueError('Importer des observations JSONL avant de commencer.')
        by_id = {e.uid: e for e in self.episodes}
        return {key: [by_id[u] for u in ids] for key, ids in self.report['manifest'].items()}

    def begin(self, config: Protocol):
        self.idle_required(); splits = self.get_splits()
        schedule = curriculum(splits, config.epochs, config.seed) if config.mode == 'curriculum' else (
            [('C / naturel chronologique', e) for e in splits['train']] +
            [('validation / gelee', e) for e in splits['validation']])
        old = self.state_dict()
        self.agents = new_agents(config.seed); self.agents['motifs'].reversal = config.reversal
        self.config = config; self.position = 0; self.metrics = {}; self.last = None
        self.session = f'{time.time_ns()}-{config.seed}'; self.kind = 'training'
        self.plan = [{'phase': p, 'uid': e.uid} for p, e in schedule]
        self.eval_used = 0; self.frozen_agents = None; self.origin_dataset = self.report['fingerprint']
        self.log('Nouveau protocole : trois apprenants vierges. Aucun poids live ni checkpoint final importe.')
        self.log('Equilibrage uniquement dans TRAIN. Validation gelee, TEST reserve et non entraine.')
        try: self._save()
        except Exception: self._restore(old); raise

    def begin_test(self):
        self.idle_required()
        if not self.session or self.position != len(self.plan) or self.kind not in ('training', 'test'):
            raise ValueError('Terminer le protocole avant le test reserve.')
        test = self.get_splits()['test']
        old = self.state_dict()
        self.eval_used += 1; self.kind = 'test'
        self.plan.extend({'phase': f'test / lecture {self.eval_used}', 'uid': e.uid} for e in test)
        self.log('TEST gele : aucune plasticite. Revoir ses resultats en fait un test explore, pas une preuve independante.')
        try: self._save()
        except Exception: self._restore(old); raise

    def _episode(self, uid):
        return next(e for e in self.episodes if e.uid == uid)

    def prepare(self):
        if self.fatal: raise ValueError(self.fatal)
        if self.pending: raise ValueError('Un choix est deja en cours.')
        if self.position >= len(self.plan):
            self.auto = False; raise ValueError('Protocole termine. Lancer le test reserve ou un nouveau protocole.')
        item = self.plan[self.position]; ep = self._episode(item['uid'])
        frozen = item['phase'].startswith(('validation', 'test'))
        before = {k: b.to_dict() for k, b in self.agents.items()}
        decisions = {key: brain.decide(ep.observation(), 0. if frozen else self.config.epsilon,
                                      self.config.circuit()) for key, brain in self.agents.items()}
        self.pending = {'item': item, 'episode': ep, 'decisions': decisions,
                        'before': before, 'frozen': frozen}
        self.next_at = time.monotonic()+self.config.delay

    def resolve(self):
        if not self.pending: raise ValueError('Aucun choix a resoudre.')
        pending = self.pending; ep = pending['episode']; phase = pending['item']['phase']
        old = self.state_dict(); old['agents'] = pending['before']
        metric = self.metrics.setdefault(phase, stats_blank()); metric['n'] += 1
        metric['always'] = [v+int(ep.touches[i]) for i, v in enumerate(metric['always'])]
        metric['random_expected_wins'] += sum(ep.touches)/3
        metric['oracle_wins'] += int(any(ep.touches))
        category = ep.touches.index(True) if sum(ep.touches) == 1 else None
        if category is not None: metric['exclusive_n'][category] += 1
        results = {}; outcomes = {}; neuron = None
        for key, brain in self.agents.items():
            d = pending['decisions'][key]; reward = 1. if ep.touches[d.action] else -1.
            enabled = self.config.plasticity and not pending['frozen']
            result = brain.reinforce(d, reward, self.config.learning_rate, enabled=enabled,
                                     credit_delay=self.config.credit_delay)
            results[key] = result
            m = metric['agents'][key]; m['wins'] += int(reward > 0); m['points'] += int(reward)
            m['choices'][d.action] += 1; m['wins_chosen'][d.action] += int(reward > 0)
            m['score_rows'].append([d.scores.tolist(), list(ep.touches)])
            active = set(np.flatnonzero(d.kc > 0).tolist())
            m['kc_counts'].append(len(active)); m['kc_union'] = sorted(set(m['kc_union']) | active)
            if category is not None: metric['exclusive_correct'][key][category] += int(d.action == category)
            outcomes[key] = {'action': d.action, 'hit': reward > 0, 'reward': reward,
                             'scores_before': d.scores.tolist(), 'exploratory': d.exploratory,
                             'updated': enabled, 'kc_active': len(active)}
            if key == 'motifs': neuron = brain.neural(d, result)
        self.position += 1
        record = {'index': self.position, 'session': self.session, 'phase': phase, 'uid': ep.uid,
                  'placed': ep.placed, 'end': ep.end, 'touches': list(ep.touches),
                  'category': ep.category, 'outcomes': outcomes, 'frozen': pending['frozen'],
                  'dataset': self.report['fingerprint'], 'config': self.config.model_dump()}
        self.last = {**record, 'neural': neuron, 'episode': ep.to_dict(),
                     'result': results['motifs']}
        # Evaluation cannot mutate even RNG state of the deployable agents.
        if pending['frozen']: self.agents = restore_agents(pending['before'])
        if self.position == len(self.plan): self.auto = False
        self.pending = None
        self.log(f"{self.position}/{len(self.plan)} {phase} : motifs {ACTIONS[outcomes['motifs']['action']]} {outcomes['motifs']['reward']:+.0f} ; reference {outcomes['reference']['reward']:+.0f}.")
        try: self._save(record)
        except Exception:
            self._restore(old); self.auto = False; self.pending = None
            self.fatal = 'Echec de sauvegarde : poids restaures. Verifier le stockage puis redemarrer.'
            raise
        self.next_at = time.monotonic()+.05

    def tick(self):
        if self.fatal: return
        now = time.monotonic()
        if self.pending and now >= self.next_at: self.resolve()
        elif self.auto and now >= self.next_at:
            if self.position < len(self.plan): self.prepare()
            else: self.auto = False

    def set_auto(self, enabled):
        if enabled and (not self.plan or self.position >= len(self.plan)):
            raise ValueError('Creer un protocole non termine avant le lancement.')
        if self.fatal: raise ValueError(self.fatal)
        self.auto = bool(enabled)

    def summary_metrics(self):
        out = {}
        for phase, s in self.metrics.items():
            n = s['n']; agents = {}
            for key, a in s['agents'].items():
                ex = [s['exclusive_correct'][key][i]/count for i, count in enumerate(s['exclusive_n']) if count]
                agents[key] = {'wins': a['wins'], 'points': a['points'],
                    'rate': a['wins']/n if n else None, 'choices': a['choices'],
                    'wins_chosen': a['wins_chosen'], 'wilson_descriptive': wilson(a['wins'], n),
                    'balanced_exclusive': float(np.mean(ex)) if len(ex) == 3 else None,
                    'auc': [auc([r[0][i] for r in a['score_rows']], [r[1][i] for r in a['score_rows']]) for i in range(3)],
                    'mean_active_kc': float(np.mean(a['kc_counts'])) if a['kc_counts'] else None,
                    'kc_ever_active': len(a['kc_union'])}
            out[phase] = {'n': n, 'agents': agents, 'always': s['always'],
                          'random_expected_wins': s['random_expected_wins'],
                          'oracle_wins': s['oracle_wins'], 'exclusive_n': s['exclusive_n']}
        return out

    def public_dataset(self, manifest=False):
        if not self.report:
            return None
        out = copy.deepcopy(self.report)
        if not manifest:
            out.pop('manifest', None)
        # Hide test-label aggregates too: future class proportions must not guide
        # manual tuning before the explicit opening of TEST.
        if not self.eval_used:
            out['splits']['test']['categories'] = None
            out['splits']['test']['touch_rates'] = None
        return out

    def snapshot(self):
        active = None
        if self.pending:
            ep = self.pending['episode']; d = self.pending['decisions']['motifs']
            # No target ticks, touches, outcome or full episode in public pending state.
            active = {'phase': self.pending['item']['phase'], 'position': self.position+1,
                      'placed': ep.placed, 'start': ep.start, 'end': ep.end,
                      'reference': ep.reference, 'base_row': ep.base_row,
                      'history': ep.history, 'history_end': ep.history_end,
                      'chosen': d.action, 'scores': d.scores.tolist(),
                      'neural': self.agents['motifs'].neural(d)}
        return {'version': '0.5.0-alpha', 'dataset': self.public_dataset(),
                'import': self.import_report, 'session': self.session, 'config': self.config.model_dump(),
                'auto': self.auto, 'pending': active, 'position': self.position,
                'total': len(self.plan), 'kind': self.kind, 'test_views': self.eval_used,
                'metrics': self.summary_metrics(), 'last': self.last if not active else None,
                'logs': self.logs, 'error': self.fatal,
                'updates': {k: a.updates for k, a in self.agents.items()}}

    def export_report(self):
        records = [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM records WHERE session=? ORDER BY step', (self.session,))]
        return {'schema': 1, 'version': '0.5.0-alpha', 'timestamp': stamp(),
                'dataset': self.public_dataset(manifest=True), 'import': self.import_report, 'session': self.session,
                'protocol': self.config.model_dump(), 'metrics': self.summary_metrics(),
                'records': records, 'test_views': self.eval_used,
                'limitations': ['Replay de donnees deja examinees : pas une evaluation prospective vierge.',
                    'Selection equilibree par resultats seulement dans TRAIN : distribution modifiee.',
                    'Les repetitions ne sont pas de nouvelles observations independantes.',
                    'Modeles synthetiques, pas une reconstruction MaleCNS.',
                    'Points +1/-1 : pas des profits et pas des probabilites calibrees.']}
