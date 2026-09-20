"""Synthetic rate-based mushroom-body-inspired bandit, not a MaleCNS simulation.

No prices from the future, scenario names, or outcome labels enter this module.
Rates are non-negative. A signed score is the difference between two artificial
MBON channels. The rule is a bounded, action-gated delta rule, not a measured
Drosophila plasticity law. See MODELE.md.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import numpy as np

ACTIONS = ("hausse", "stable", "baisse")
N_KC = 256
N_ACTIVE = 16
SCHEMA = 1


@dataclass(frozen=True)
class Observation:
    """Information available at decision time only."""
    history: tuple[float, ...]
    neutral_pct: float
    horizon_steps: int


@dataclass
class Decision:
    action: int
    exploratory: bool
    kc: np.ndarray
    mbon_plus: np.ndarray
    mbon_minus: np.ndarray
    scores: np.ndarray
    features: np.ndarray
    extra: dict[str, Any] | None = None


class MushroomBody:
    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        # Eight scalar features, each encoded by nine positive tuning curves.
        self.centers = np.linspace(-1.0, 1.0, 9)
        self.projection = np.zeros((8 * 9, N_KC))
        for k in range(N_KC):
            ix = self.rng.choice(8 * 9, 6, replace=False)
            values = self.rng.uniform(0.5, 1.5, 6)
            self.projection[ix, k] = values / values.sum()
        self.tie_break = self.rng.uniform(0.0, 1e-9, N_KC)
        self.w_plus = np.full((N_KC, 3), 0.5)
        self.w_minus = np.full((N_KC, 3), 0.5)
        self.updates = 0

    @staticmethod
    def features(obs: Observation) -> np.ndarray:
        p = np.asarray(obs.history, dtype=float)
        if len(p) < 4 or not np.all(np.isfinite(p)) or np.any(p <= 0):
            raise ValueError("Historique invalide : au moins 4 prix positifs finis.")
        r = (p[1:] / p[:-1] - 1) * 100  # Percentage-point returns.
        # Fixed scale: there is no fit to future data or global dataset.
        scale = 0.15
        raw = np.array([
            r[-1] / scale,
            r[-3:].mean() / scale,
            r.mean() / scale,
            r.std() / scale,
            (r[-1] - r[-2]) / scale,
            ((p[-1] / p[0] - 1) * 100) / (scale * np.sqrt(len(r))),
            obs.neutral_pct / 0.3,
            obs.horizon_steps / 12,
        ])
        return np.tanh(raw)

    def encode(self, obs: Observation) -> tuple[np.ndarray, np.ndarray]:
        f = self.features(obs)
        pn = np.exp(-0.5 * ((f[:, None] - self.centers[None, :]) / 0.30) ** 2)
        drive = pn.reshape(-1) @ self.projection + self.tie_break
        winners = np.argsort(drive)[-N_ACTIVE:]
        kc = np.zeros(N_KC)
        kc[winners] = 1.0
        return kc, f

    def outputs(self, kc: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        normalizer = max(1.0, float(kc.sum()))
        plus = kc @ self.w_plus / normalizer
        minus = kc @ self.w_minus / normalizer
        return plus, minus, plus - minus

    def decide(self, obs: Observation, epsilon: float = 0.1) -> Decision:
        if not 0 <= epsilon <= 1:
            raise ValueError("Exploration hors limites.")
        kc, features = self.encode(obs)
        plus, minus, q = self.outputs(kc)
        exploratory = bool(self.rng.random() < epsilon)
        if exploratory:
            action = int(self.rng.integers(3))
        else:
            candidates = np.flatnonzero(np.isclose(q, q.max(), atol=1e-12, rtol=0))
            action = int(self.rng.choice(candidates))  # Fair initial ties.
        return Decision(action, exploratory, kc, plus, minus, q, features)

    def reinforce(self, decision: Decision, reward: float, learning_rate: float,
                  enabled: bool = True) -> dict[str, Any]:
        if reward not in (-1.0, 1.0) or not 0 < learning_rate <= 1:
            raise ValueError("Renforcement invalide.")
        # Eligibility is the ACTUAL decision-time KC pattern, not the final price.
        delta = float(reward - decision.scores[decision.action])
        delta = float(np.clip(delta, -2, 2))
        old_plus = self.w_plus[:, decision.action].copy()
        old_minus = self.w_minus[:, decision.action].copy()
        if enabled:
            change = 0.5 * learning_rate * delta * decision.kc
            self.w_plus[:, decision.action] = np.clip(old_plus + change, 0, 1)
            self.w_minus[:, decision.action] = np.clip(old_minus - change, 0, 1)
            self.updates += 1
        mean_change = float((np.abs(self.w_plus[:, decision.action] - old_plus).sum()
                            + np.abs(self.w_minus[:, decision.action] - old_minus).sum())
                           / (2 * N_ACTIVE))
        plus, minus, q = self.outputs(decision.kc)
        return {
            "reward": reward, "delta": delta,
            "dan_plus": max(0.0, delta) / 2,
            "dan_minus": max(0.0, -delta) / 2,
            "weight_change": mean_change, "updated": enabled,
            "post_plus": plus.tolist(), "post_minus": minus.tolist(),
            "post_scores": q.tolist(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "seed": self.seed, "updates": self.updates,
                "rng": copy.deepcopy(self.rng.bit_generator.state),
                "projection": self.projection.tolist(),
                "tie_break": self.tie_break.tolist(),
                "w_plus": self.w_plus.tolist(), "w_minus": self.w_minus.tolist()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MushroomBody":
        if data.get("schema") != SCHEMA:
            raise ValueError("Version de sauvegarde incompatible.")
        model = cls(int(data["seed"]))
        for name, shape in (("projection", (72, N_KC)), ("tie_break", (N_KC,)),
                            ("w_plus", (N_KC, 3)), ("w_minus", (N_KC, 3))):
            value = np.asarray(data[name], dtype=float)
            if value.shape != shape or not np.all(np.isfinite(value)):
                raise ValueError(f"Sauvegarde invalide : {name}.")
            if np.any(value < 0) or np.any(value > 1):
                raise ValueError(f"Valeur hors limites : {name}.")
            setattr(model, name, value)
        model.rng.bit_generator.state = data["rng"]
        model.updates = int(data["updates"])
        return model
