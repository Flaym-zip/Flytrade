"""Five experimental MB motifs, NOT a MaleCNS reconstruction.

A candidate action is encoded as a *known question*, never as its future label.
Five MBON-like scalars value each question; the 3-way argmax is an external
interface. Equations, counts, signs and rates are engineering assumptions.
See docs/MODELE-04.md for the biological evidence / implementation boundary.
"""
from __future__ import annotations
import copy
from dataclasses import asdict
from typing import Any
import numpy as np
from .brain import Decision
from .compartment_brain import CircuitSettings
from .live import GridObservation

ENCODER = 'grid-candidate-0.5-v2'
N_KC, N_FEATURES, N_PN = 1024, 20, 180
MOTIFS = ('gamma1pedc / PPL1', 'gamma2alpha1 / PPL1', 'gamma4 / PAM',
          'gamma5 / PAM', 'beta2a / PAM (revision)')
FEATURE_NAMES = ('deplacement_1s', 'deplacement_3s', 'deplacement_10s',
                 'deplacement_30s', 'vitesse_rapide', 'vitesse_lente',
                 'acceleration', 'volatilite_10s', 'volatilite_30s',
                 'persistance', 'changements_direction', 'position_case',
                 'distance_borne_basse', 'distance_borne_haute',
                 'temps_avant_fenetre', 'variation_recente_moins_ancienne',
                 'candidat_hausse', 'candidat_stable', 'candidat_baisse',
                 'distance_centre_candidat')
RATES = np.array([0.8, 0.55, 0.4, 0.25, 0.65])
MIX = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
# Mathematical readout polarity, not a neurotransmitter prediction.
POLARITY = np.array([1., 1., -1., -1., -1.])
TAU = np.array([4., 8., 12., 24., 8.])  # Model units, not biological seconds.


class MotifBrain:
    def __init__(self, seed: int = 42) -> None:
        self.seed = int(seed)
        self.rng = np.random.default_rng(seed)
        self.centers = np.linspace(-1, 1, 9)
        # Every KC samples five distinct context features and one candidate cue.
        # This gate is the artificial trading interface, not measured anatomy.
        features = np.stack([np.r_[self.rng.choice(16, 5, replace=False),
                                      self.rng.integers(16, 20)] for _ in range(N_KC)])
        self.indices = features * 9 + self.rng.integers(0, 9, (N_KC, 6))
        self.input_weights = self.rng.uniform(.8, 1.2, (N_KC, 6))
        self.input_weights /= self.input_weights.sum(axis=1, keepdims=True)
        self.weights = np.full((5, N_KC), .5)
        self.updates = 0
        self.reversal = True

    @staticmethod
    def context(obs: GridObservation) -> np.ndarray:
        p = np.asarray(obs.history, dtype=float)
        if len(p) < 11 or not np.isfinite(p).all() or np.any(p <= 0):
            raise ValueError('Historique incomplet ou invalide.')
        if not np.isfinite(obs.row_fraction) or not 0 <= obs.row_fraction < 1.000001:
            raise ValueError('Position de case invalide.')
        # The most recent tick can be newer than the one-second history sample.
        # Both are known at placement; keep the old encoder unchanged for control.
        if obs.reference_price is not None:
            if not np.isfinite(obs.reference_price) or obs.reference_price <= 0:
                raise ValueError('Prix de reference invalide.')
            p = p.copy(); p[-1] = obs.reference_price
        r = np.diff(p) / .5
        fast = slow = float(r[0])
        for x in r[1:]:
            fast = .6*x + .4*fast
            slow = .15*x + .85*slow
        nz = np.sign(r[-10:][np.abs(r[-10:]) > 1e-10])
        changes = float(np.mean(nz[1:] != nz[:-1])) if len(nz) > 1 else 0.
        frac = float(np.clip(obs.row_fraction, 0, 1))
        return np.array([np.tanh(r[-1]), np.tanh(r[-3:].sum()),
            np.tanh(r[-10:].sum()/2), np.tanh(r.sum()/4), np.tanh(fast),
            np.tanh(slow), np.tanh(fast-slow), np.tanh(r[-10:].std()),
            np.tanh(r.std()), float(nz.mean()) if len(nz) else 0.,
            2*changes-1, 2*frac-1, np.tanh(2*frac), np.tanh(2*(1-frac)),
            np.clip((obs.horizon_steps-17.5)/2.5, -1, 1),
            np.tanh(r[-3:].mean()-r[-6:-3].mean())])

    def encode_candidates(self, obs: GridObservation, config: CircuitSettings
                          ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        context = self.context(obs)
        if not config.temporal:
            context[[0, 1, 2, 3, 4, 5, 6, 9, 10, 15]] = 0
        features, pns, codes, thresholds = [], [], [], []
        for a in range(3):
            query = np.full(3, -1.)
            query[a] = 1.
            f = np.r_[context, query, np.tanh((1, 0, -1)[a]+.5-obs.row_fraction)]
            pn = np.exp(-.5*((f[:, None]-self.centers)/.24)**2).ravel()
            drive = (pn[self.indices]*self.input_weights).sum(axis=1)
            # Global sparse threshold: reduced APL-like competition, not an ODE.
            threshold = float(np.quantile(drive, .95)) if config.apl else .0
            kc = np.clip((drive-threshold)/max(float(drive.max()-threshold), 1e-9), 0, 1)
            kc[kc < 1e-6] = 0
            features.append(f); pns.append(pn); codes.append(kc); thresholds.append(threshold)
        return np.array(codes), np.array(features), np.array(pns), np.array(thresholds)

    @staticmethod
    def mixing(config: CircuitSettings) -> np.ndarray:
        # Keep one appetitive/aversive pair when compartment diversity is ablated.
        return MIX.copy() if config.compartments else np.array([.5, 0, .5, 0, 0])

    def evaluate(self, codes: np.ndarray, config: CircuitSettings):
        activities = self.weights @ codes.T / np.maximum(codes.sum(axis=1), 1e-12)[None, :]
        values = 2*POLARITY[:, None]*(activities-.5)
        q = self.mixing(config) @ values
        return activities, values, q

    def decide(self, obs: GridObservation, epsilon: float = .1,
               config: CircuitSettings | None = None) -> Decision:
        if not np.isfinite(epsilon) or not 0 <= epsilon <= 1:
            raise ValueError('Exploration invalide.')
        config = config or CircuitSettings()
        codes, features, pns, thresholds = self.encode_candidates(obs, config)
        activity, values, q = self.evaluate(codes, config)
        explore = bool(self.rng.random() < epsilon)
        a = int(self.rng.integers(3)) if explore else int(self.rng.choice(
            np.flatnonzero(np.isclose(q, q.max(), rtol=0, atol=1e-12))))
        return Decision(a, explore, codes[a], (q+1)/2, (1-q)/2, q, features[a], {
            'config': asdict(config), 'pn': pns[a].tolist(), 'apl': float(thresholds[a]),
            'codes': codes.tolist(), 'activities': activity.tolist(), 'values': values.tolist(),
            'features_all': features.tolist(), 'reversal': self.reversal,
            'mbon_activity': activity[:, a].tolist()})

    def reinforce(self, decision: Decision, reward: float, learning_rate: float,
                  enabled: bool = True, credit_delay: float = 0.) -> dict[str, Any]:
        if reward not in (-1., 1.) or not np.isfinite(learning_rate) or not 0 < learning_rate <= 1:
            raise ValueError('Renforcement invalide.')
        if not np.isfinite(credit_delay) or not 0 <= credit_delay <= 240:
            raise ValueError('Delai invalide.')
        extra = decision.extra
        if extra is None:
            raise ValueError('Contexte neuronal manquant.')
        cfg = CircuitSettings.from_dict(extra['config'])
        mix = self.mixing(cfg); active = mix > 0
        before = np.array(extra['activities'])[:, decision.action]
        # Appetitive teaching depresses avoidance-like readouts; punishment
        # depresses approach-like readouts. Opposite changes are a surrogate
        # restoration rule, NOT negative dopamine concentrations.
        targets = .5 + .5*POLARITY*reward
        delta = targets-before if cfg.feedback else targets-.5
        # Experimental omission gate: surprising positive feedback after
        # a learned aversive expectation in motif gamma2 increases beta2 update.
        omission = max(0., 1-2*before[1]) * max(0., reward)
        omission *= float(extra['reversal'] and cfg.feedback and cfg.compartments)
        delta[4] -= .25*omission
        trace = np.exp(-credit_delay/TAU) if cfg.eligibility else np.ones(5)
        kc = decision.kc
        norm = float(kc.sum()/max(kc@kc, 1e-12))
        old = self.weights.copy()
        if enabled:
            if cfg.forgetting:
                self.weights[active] = .5 + .999*(self.weights[active]-.5)
            self.weights[active] = np.clip(self.weights[active] +
                (learning_rate*RATES[active]*trace[active]*delta[active])[:, None] *
                kc[None, :]*norm, 0, 1)
            self.updates += 1
        activity, values, scores = self.evaluate(np.array(extra['codes']), cfg)
        changes = np.abs(self.weights-old).sum(axis=1)/max(1, np.count_nonzero(kc))
        d = reward - float(decision.scores[decision.action])
        compartments = []
        for c, name in enumerate(MOTIFS):
            compartments.append({'name': name, 'active': bool(active[c]),
                'scores': values[c].tolist(), 'delta': float(delta[c]),
                'dan_plus': float(max(0, d)/2 if c >= 2 else 0),
                'dan_minus': float(max(0, -d)/2 if c < 2 else 0),
                'induction': float(max(0, -delta[c])), 'restoration': float(max(0, delta[c])),
                'weight_change': float(changes[c]), 'activity': float(activity[c, decision.action]),
                'eligibility': float(trace[c]), 'rate_factor': float(RATES[c])})
        return {'reward': reward, 'delta': d, 'dan_plus': max(0, d)/2,
            'dan_minus': max(0, -d)/2, 'updated': bool(enabled),
            'weight_change': float(mix@changes), 'compartments': compartments,
            'omission_gate': float(omission), 'post_scores': scores.tolist(),
            'post_plus': ((scores+1)/2).tolist(), 'post_minus': ((1-scores)/2).tolist(),
            'credit_delay': float(credit_delay), 'circuit_settings': asdict(cfg)}

    def neural(self, decision: Decision | None, result: dict | None = None) -> dict:
        if decision is None:
            return {'kc': [], 'pn': [], 'apl': 0, 'scores': [0]*3,
                    'mbon_plus': [0]*3, 'mbon_minus': [0]*3, 'dan_plus': 0,
                    'dan_minus': 0, 'compartments': []}
        cfg = CircuitSettings.from_dict(decision.extra['config'])
        act, values, q = self.evaluate(np.array(decision.extra['codes']), cfg)
        comps = result['compartments'] if result else [
            {'name': name, 'active': bool(self.mixing(cfg)[c]),
             'scores': values[c].tolist(), 'dan_plus': 0, 'dan_minus': 0,
             'weight_change': 0, 'activity': float(act[c, decision.action])}
            for c, name in enumerate(MOTIFS)]
        return {'kc': decision.kc.tolist(), 'pn': decision.extra['pn'],
            'apl': decision.extra['apl'], 'scores': q.tolist(),
            'mbon_plus': ((q+1)/2).tolist(), 'mbon_minus': ((1-q)/2).tolist(),
            'dan_plus': result['dan_plus'] if result else 0,
            'dan_minus': result['dan_minus'] if result else 0, 'compartments': comps}

    def to_dict(self) -> dict[str, Any]:
        return {'schema': 1, 'kind': 'synthetic-mb-motifs', 'encoder': ENCODER,
            'seed': self.seed, 'updates': self.updates, 'reversal': self.reversal,
            'rng': copy.deepcopy(self.rng.bit_generator.state),
            'indices': self.indices.tolist(), 'input_weights': self.input_weights.tolist(),
            'weights': self.weights.tolist()}

    @classmethod
    def from_dict(cls, data: dict) -> 'MotifBrain':
        if data.get('kind') != 'synthetic-mb-motifs' or data.get('schema') != 1 or data.get('encoder') != ENCODER:
            raise ValueError('Checkpoint des motifs incompatible.')
        model = cls(int(data['seed']))
        for key, shape in [('indices', (N_KC, 6)), ('input_weights', (N_KC, 6)), ('weights', (5, N_KC))]:
            a = np.asarray(data[key])
            if a.shape != shape or not np.isfinite(a).all() or np.any(a < 0):
                raise ValueError('Poids invalides : '+key)
            if key == 'indices':
                if not np.issubdtype(a.dtype, np.integer) or np.any(a >= N_PN):
                    raise ValueError('Indices PN invalides.')
            elif np.any(a > 1):
                raise ValueError('Poids hors limites.')
            setattr(model, key, a.copy())
        if not np.allclose(model.input_weights.sum(axis=1), 1):
            raise ValueError('Projection non normalisee.')
        model.updates = int(data['updates'])
        if model.updates < 0 or type(data['reversal']) is not bool:
            raise ValueError('Metadonnees invalides.')
        model.reversal = data['reversal']
        model.rng.bit_generator.state = data['rng']
        return model
