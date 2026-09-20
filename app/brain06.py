"""Synthetic KC ensemble with a separate bounded economic-value readout.
No MaleCNS cell IDs. Forecast learning remains binary; value learning is separate.
"""
from __future__ import annotations
import copy
import hashlib
import json
import numpy as np
from .decision_brain import DecisionBrain

ENCODER06 = 'grid-liquidity-risk-v4'

class Brain06(DecisionBrain):
    def __init__(self, seed=42, sparsity=.05, use_liquidity=False, n_kc=2048):
        if n_kc not in (1024, 2048):
            raise ValueError('Choisir 1024 ou 2048 KC')
        if not .01 <= sparsity <= .5:
            raise ValueError('Activite KC invalide')
        self.n_kc = int(n_kc)
        self.seed = int(seed)
        self.sparsity = float(sparsity)
        self.use_liquidity = bool(use_liquidity)
        self.rng = np.random.default_rng(seed)
        self.centers = np.linspace(-1, 1, 9)
        indices = []
        for _ in range(n_kc):
            if use_liquidity:
                f = np.r_[self.rng.choice(16, 4, replace=False), self.rng.integers(16, 24), self.rng.integers(24, 28)]
            else:
                f = np.r_[self.rng.choice(16, 5, replace=False), self.rng.integers(24, 28)]
            indices.append(f * 9 + self.rng.integers(0, 9, 6))
        self.indices = np.array(indices)
        self.input_weights = self.rng.uniform(.8, 1.2, (n_kc, 6))
        self.input_weights /= self.input_weights.sum(axis=1, keepdims=True)
        self.weights = np.full((5, n_kc), .5)
        self.value_weights = np.zeros(n_kc)
        self.updates = 0
        self.value_updates = 0
        self.visits = np.zeros(n_kc, dtype=np.int64)
        self.book = None
        self.reversal = True

    def predict(self, obs, book=None, epsilon=0., config=None):
        d = super().predict(obs, book, epsilon, config)
        codes = np.asarray(d.extra['codes'])
        d.extra['economic_values'] = (codes @ self.value_weights / np.maximum(codes.sum(axis=1), 1e-12)).tolist()
        return d

    def learn_financial(self, decision, net_per_unit, rate=.1):
        """Post-outcome only. Missing execution quotes -> no value update.
        tanh(net per unit / 2) is an explicit utility, NOT expected euros or p(hit).
        Forecast weights and their calibration fingerprint are unaffected.
        """
        codes = np.asarray(decision.extra['codes'])
        old = self.value_weights.copy()
        changes = []
        signals = []
        for a, net in enumerate(net_per_unit):
            if net is None:
                continue
            if not np.isfinite(net):
                raise ValueError('Resultat financier non fini')
            target = float(np.tanh(net / 2.))
            c = codes[a]
            prediction = float(decision.extra['economic_values'][a])
            norm = c.sum() / max(float(c @ c), 1e-12)
            changes.append(rate * (target - prediction) * c * norm)
            signals.append({'action': a, 'net_per_unit': net, 'target': target,
                            'error': target - prediction})
        if changes:
            self.value_weights = np.clip(old + np.mean(changes, axis=0), -1, 1)
            self.value_updates += 1
        return {'signals': signals, 'updated': bool(changes),
                'weight_change': float(np.mean(np.abs(self.value_weights - old)))}

    def neural(self, d, result=None):
        n = super().neural(d, result)
        n.update(n_kc=self.n_kc, encoder=ENCODER06, value_updates=self.value_updates,
                 economic_values=d.extra.get('economic_values', [0]*3) if d else [0]*3)
        return n

    def to_dict(self):
        d = super().to_dict()
        d.update(kind='synthetic-risk06', encoder=ENCODER06, n_kc=self.n_kc,
                 value_weights=self.value_weights.tolist(), value_updates=self.value_updates)
        return d

    def fingerprint(self):
        d = self.to_dict()
        for k in ('visits', 'rng', 'updates', 'value_weights', 'value_updates'):
            d.pop(k, None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    @classmethod
    def from_dict(cls, d):
        if d.get('kind') != 'synthetic-risk06' or d.get('encoder') != ENCODER06:
            raise ValueError('Poids incompatibles : reentrainer en alpha06, pas de conversion implicite')
        b = cls(int(d['seed']), float(d['sparsity']), bool(d['use_liquidity']), int(d['n_kc']))
        for key, shape in [('indices',(b.n_kc,6)), ('input_weights',(b.n_kc,6)),
                           ('weights',(5,b.n_kc)), ('visits',(b.n_kc,)), ('value_weights',(b.n_kc,))]:
            v = np.array(d[key])
            if v.shape != shape or not np.isfinite(v).all():
                raise ValueError('Dimensions/valeurs invalides : '+key)
            if key in ('indices','visits') and (not np.issubdtype(v.dtype,np.integer) or np.any(v<0)):
                raise ValueError('Entiers positifs attendus')
            if key=='indices' and np.any(v>=252): raise ValueError('Index PN invalide')
            if key in ('weights','input_weights') and (np.any(v<0) or np.any(v>1)): raise ValueError('Poids hors bornes')
            if key=='value_weights' and np.any(np.abs(v)>1): raise ValueError('Valeur hors bornes')
            setattr(b,key,v.copy())
        if not np.allclose(b.input_weights.sum(axis=1),1): raise ValueError('Projection non normalisee')
        b.updates=int(d['updates']); b.value_updates=int(d.get('value_updates',0))
        if min(b.updates,b.value_updates)<0: raise ValueError('Compteurs invalides')
        b.reversal=bool(d['reversal']);b.rng.bit_generator.state=copy.deepcopy(d['rng'])
        return b
