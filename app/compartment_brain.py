"""Synthetic compartmental mushroom body, not an imported connectome.

The biological motifs are referenced in MODELE.md. All numerical parameters,
three financial action channels, and update equations are engineering choices.
No scenario name, future return, class label, or wall clock reaches this module.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .brain import Decision, MushroomBody, Observation

N_KC = 1024
N_PN = 108
N_COMPARTMENTS = 3
COMPARTMENTS = ("Rapide", "Intermediaire", "Lente")
LEARNING_MULTIPLIERS = np.array([1.0, 0.35, 0.08])
RETENTION = np.array([0.97, 0.995, 0.9995])
ELIGIBILITY_TAU = np.array([4.0, 16.0, 64.0])  # Arbitrary simulated delay units.
MIX = np.array([0.55, 0.30, 0.15])
SCHEMA = 2


@dataclass(frozen=True)
class CircuitSettings:
    temporal: bool = True
    apl: bool = True
    compartments: bool = True
    feedback: bool = True
    eligibility: bool = True
    forgetting: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CircuitSettings":
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError("Reglage neuronal inconnu.")
        if any(type(v) is not bool for v in data.values()):
            raise ValueError("Les reglages neuronaux doivent etre booleens.")
        return cls(**data)


class CompartmentBrain:
    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.centers = np.linspace(-1.0, 1.0, 9)
        self.input_indices = np.stack([self.rng.choice(N_PN, 6, replace=False)
                                       for _ in range(N_KC)])
        self.input_weights = self.rng.uniform(0.5, 1.5, (N_KC, 6))
        self.input_weights /= self.input_weights.sum(axis=1, keepdims=True)
        self.w_plus = np.full((N_COMPARTMENTS, N_KC, 3), 0.5)
        self.w_minus = np.full((N_COMPARTMENTS, N_KC, 3), 0.5)
        self.updates = 0

    @staticmethod
    def features(obs: Observation, temporal: bool = True) -> np.ndarray:
        base = MushroomBody.features(obs)
        p = np.asarray(obs.history, dtype=float)
        r = (p[1:] / p[:-1] - 1) * 100
        fast = slow = float(r[0])
        # These are causal sensory filters, not extra observations of the future.
        for value in r[1:]:
            fast = 0.6 * value + 0.4 * fast
            slow = 0.15 * value + 0.85 * slow
        previous = float(r[-6:-3].mean()) if len(r) >= 6 else float(r[0])
        extra = np.tanh(np.array([fast, slow, fast - slow, previous]) / 0.15)
        if not temporal:
            extra[:] = 0  # Constant channels: no temporal information in them.
        return np.concatenate([base, extra])

    def encode(self, obs: Observation, config: CircuitSettings | None = None
               ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        config = config or CircuitSettings()
        features = self.features(obs, config.temporal)
        pn = np.exp(-0.5 * ((features[:, None] - self.centers) / 0.30) ** 2).ravel()
        drive = (pn[self.input_indices] * self.input_weights).sum(axis=1)
        kc = np.zeros(N_KC)
        apl = 0.0
        trajectory = []
        # Reduced rate dynamics KC -> APL -> KC, settled afresh on each stimulus.
        # No membrane voltages or spikes are represented.
        for step in range(96):
            inhibition = 6.0 * apl if config.apl else 0.0
            target = np.clip(4.0 * (drive - 0.32 - inhibition), 0, 1)
            kc += 0.2 * (target - kc)
            apl += 0.08 * (float(kc.mean()) - apl) if config.apl else 0.0
            if step % 4 == 0 or step == 95:
                trajectory.append({"step": step + 1, "kc_mean": float(kc.mean()),
                                   "apl": float(apl)})
        # Remove numerical tails of units that became silent during settling.
        kc[kc < 1e-3] = 0
        extra = {"pn": pn.tolist(), "apl": float(apl),
                 "inhibition": float(6 * apl if config.apl else 0),
                 "settling": trajectory, "config": asdict(config)}
        return kc, features, extra

    @staticmethod
    def mixing(config: CircuitSettings) -> np.ndarray:
        return MIX.copy() if config.compartments else np.array([1.0, 0.0, 0.0])

    def compartment_outputs(self, kc: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        divisor = max(1e-12, float(kc.sum()))
        plus = np.einsum('k,cka->ca', kc, self.w_plus) / divisor
        minus = np.einsum('k,cka->ca', kc, self.w_minus) / divisor
        return plus, minus, plus - minus

    def outputs(self, kc: np.ndarray, config: CircuitSettings | None = None
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        plus, minus, _ = self.compartment_outputs(kc)
        mix = self.mixing(config or CircuitSettings())
        p, m = mix @ plus, mix @ minus
        return p, m, p - m

    def decide(self, obs: Observation, epsilon: float = 0.1,
               config: CircuitSettings | None = None) -> Decision:
        if not np.isfinite(epsilon) or not 0 <= epsilon <= 1:
            raise ValueError("Exploration hors limites.")
        config = config or CircuitSettings()
        kc, features, extra = self.encode(obs, config)
        plus, minus, scores = self.outputs(kc, config)
        _, _, comp_scores = self.compartment_outputs(kc)
        exploratory = bool(self.rng.random() < epsilon)
        if exploratory:
            action = int(self.rng.integers(3))
        else:
            candidates = np.flatnonzero(np.isclose(scores, scores.max(), atol=1e-12, rtol=0))
            action = int(self.rng.choice(candidates))
        extra.update({"comp_scores_before": comp_scores.tolist(),
                      "mix": self.mixing(config).tolist()})
        return Decision(action, exploratory, kc, plus, minus, scores, features, extra)

    def reinforce(self, decision: Decision, reward: float, learning_rate: float,
                  enabled: bool = True, credit_delay: float = 0.0) -> dict[str, Any]:
        if reward not in (-1.0, 1.0) or not np.isfinite(learning_rate) or not 0 < learning_rate <= 1:
            raise ValueError("Renforcement invalide.")
        if not np.isfinite(credit_delay) or not 0 <= credit_delay <= 240:
            raise ValueError("Delai de renforcement invalide.")
        if not decision.extra:
            raise ValueError("Decision sans contexte de compartiment.")
        config = CircuitSettings.from_dict(decision.extra["config"])
        before_scores = np.asarray(decision.extra["comp_scores_before"])
        active = np.array([True, config.compartments, config.compartments])
        delta = np.clip(reward - before_scores[:, decision.action], -2, 2) if config.feedback else np.full(3, reward)
        delta = np.where(active, delta, 0)
        trace = np.exp(-credit_delay / ELIGIBILITY_TAU) if config.eligibility else np.ones(3)
        old_plus, old_minus = self.w_plus.copy(), self.w_minus.copy()
        decay_change = 0.0
        if enabled:
            # Optional forgetting, measured in completed plastic trials, not seconds.
            if config.forgetting:
                self.w_plus[active] = 0.5 + RETENTION[active, None, None] * (self.w_plus[active] - 0.5)
                self.w_minus[active] = 0.5 + RETENTION[active, None, None] * (self.w_minus[active] - 0.5)
                decay_change = float((np.abs(self.w_plus-old_plus).sum() +
                                      np.abs(self.w_minus-old_minus).sum()) / (2 * old_plus.size))
            for c in np.flatnonzero(active):
                # Three-factor surrogate: decision-time KC activity, selected action,
                # local signed teaching signal. Not literal PAM/PPL1 physiology.
                eligibility = decision.kc * trace[c]
                change = 0.5 * learning_rate * LEARNING_MULTIPLIERS[c] * delta[c] * eligibility
                self.w_plus[c, :, decision.action] = np.clip(self.w_plus[c, :, decision.action] + change, 0, 1)
                self.w_minus[c, :, decision.action] = np.clip(self.w_minus[c, :, decision.action] - change, 0, 1)
            self.updates += 1
        changed = (np.abs(self.w_plus - old_plus) + np.abs(self.w_minus - old_minus)).sum(axis=(1, 2))
        normalizer = max(1.0, 2 * np.count_nonzero(decision.kc))
        change_by_comp = changed / normalizer
        p, m, q = self.outputs(decision.kc, config)
        cp, cm, cq = self.compartment_outputs(decision.kc)
        mix = self.mixing(config)
        aggregate_delta = float(mix @ delta)
        # Separate compartments carry different error magnitudes for the same result.
        dan_plus = np.maximum(delta, 0) / 2
        dan_minus = np.maximum(-delta, 0) / 2
        return {"reward": reward, "delta": aggregate_delta,
                "dan_plus": float(mix @ dan_plus), "dan_minus": float(mix @ dan_minus),
                "weight_change": float(mix @ change_by_comp), "updated": bool(enabled),
                "post_plus": p.tolist(), "post_minus": m.tolist(), "post_scores": q.tolist(),
                "compartments": [{"name": COMPARTMENTS[c], "active": bool(active[c]),
                    "rate_factor": float(LEARNING_MULTIPLIERS[c]),
                    "mix": float(mix[c]), "tau": float(ELIGIBILITY_TAU[c]),
                    "eligibility": float(trace[c]), "delta": float(delta[c]),
                    "dan_plus": float(dan_plus[c]), "dan_minus": float(dan_minus[c]),
                    "scores_before": before_scores[c].tolist(), "scores": cq[c].tolist(),
                    "weight_change": float(change_by_comp[c])} for c in range(3)],
                "decay_change": decay_change, "credit_delay": float(credit_delay),
                "circuit_settings": asdict(config)}

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "kind": "synthetic-compartments", "seed": self.seed,
                "updates": self.updates, "rng": copy.deepcopy(self.rng.bit_generator.state),
                "input_indices": self.input_indices.tolist(), "input_weights": self.input_weights.tolist(),
                "w_plus": self.w_plus.tolist(), "w_minus": self.w_minus.tolist()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompartmentBrain":
        if data.get("schema") != SCHEMA or data.get("kind") != "synthetic-compartments":
            raise ValueError("Sauvegarde de compartiments incompatible.")
        model = cls(int(data["seed"]))
        indices = np.asarray(data["input_indices"])
        if indices.shape != (N_KC, 6) or not np.issubdtype(indices.dtype, np.integer):
            raise ValueError("Indices PN invalides.")
        if np.any(indices < 0) or np.any(indices >= N_PN):
            raise ValueError("Indices PN hors limites.")
        model.input_indices = indices.astype(int)
        for name, shape in (("input_weights", (N_KC, 6)), ("w_plus", (3, N_KC, 3)), ("w_minus", (3, N_KC, 3))):
            value = np.asarray(data[name], dtype=float)
            if value.shape != shape or not np.all(np.isfinite(value)) or np.any(value < 0) or np.any(value > 1):
                raise ValueError(f"Sauvegarde invalide : {name}.")
            setattr(model, name, value)
        if not np.allclose(model.input_weights.sum(axis=1), 1, atol=1e-12):
            raise ValueError("Normalisation PN invalide.")
        model.rng.bit_generator.state = data["rng"]
        model.updates = int(data["updates"])
        if model.updates < 0:
            raise ValueError("Compteur negatif.")
        return model
