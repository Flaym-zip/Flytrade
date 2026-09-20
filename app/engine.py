"""Controlled trials with isolated model memories and transactional checkpoints."""
from __future__ import annotations

import copy
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .brain import ACTIONS, Decision, MushroomBody, Observation
from .compartment_brain import (COMPARTMENTS, ELIGIBILITY_TAU, LEARNING_MULTIPLIERS,
                                CircuitSettings, CompartmentBrain)

VERSION = "0.5.0-alpha"
Return = Annotated[float, Field(ge=-5, le=5, allow_inf_nan=False)]
ModelKey = Literal["legacy", "compartments"]
MODEL_INFO = {
    "legacy": {"key": "legacy", "label": "Reference alpha 01", "kc": 256, "pn": 72,
               "compartments": 1, "mbon": 6, "dan": 2, "apl": 0},
    "compartments": {"key": "compartments", "label": "Compartiments alpha 02", "kc": 1024,
                     "pn": 108, "compartments": 3, "mbon": 18, "dan": 6, "apl": 1},
}


class NeuralSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temporal: StrictBool = True
    apl: StrictBool = True
    compartments: StrictBool = True
    feedback: StrictBool = True
    eligibility: StrictBool = True
    forgetting: StrictBool = False

    def circuit(self) -> CircuitSettings:
        return CircuitSettings.from_dict(self.model_dump())


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="Mon scenario", min_length=1, max_length=80)
    price: float = Field(default=100, ge=0.01, le=1e9)
    history_returns: list[Return] = Field(min_length=3, max_length=60)
    future_returns: list[Return] = Field(min_length=1, max_length=60)
    neutral_pct: float = Field(default=0.15, ge=0, le=10)
    duration: float = Field(default=6, ge=1, le=60)
    epsilon: float = Field(default=0.10, ge=0, le=1)
    learning_rate: float = Field(default=0.25, gt=0, le=1)
    plasticity: bool = True
    neural: NeuralSettings = Field(default_factory=NeuralSettings)
    credit_delay: float = Field(default=0, ge=0, le=240)

    @model_validator(mode="after")
    def bounded_path(self) -> "Scenario":
        if not self.name.strip():
            raise ValueError("Le nom ne doit pas etre vide.")
        return self

    def history_prices(self) -> list[float]:
        factors = np.cumprod([1.0] + [1 + r / 100 for r in self.history_returns])
        return (self.price * factors / factors[-1]).tolist()

    def future_prices(self) -> list[float]:
        factors = np.cumprod([1.0] + [1 + r / 100 for r in self.future_returns])
        return (self.price * factors).tolist()

    def observation(self) -> Observation:
        # The contract's horizon is observable, its future prices are not.
        return Observation(tuple(self.history_prices()), self.neutral_pct, len(self.future_returns))


def classify(final_price: float, reference: float, neutral_pct: float) -> int:
    upper = reference * (1 + neutral_pct / 100)
    lower = reference * (1 - neutral_pct / 100)
    if final_price > upper and not math.isclose(final_price, upper, rel_tol=1e-12):
        return 0
    if final_price < lower and not math.isclose(final_price, lower, rel_tol=1e-12):
        return 2
    return 1


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def restore_brain(key: str, raw: dict[str, Any]):
    return MushroomBody.from_dict(raw) if key == "legacy" else CompartmentBrain.from_dict(raw)


class Lab:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = data_dir / "state.json"
        self.models = {"legacy": MushroomBody(), "compartments": CompartmentBrain()}
        self.active_model = "compartments"
        self.trials: list[dict[str, Any]] = []
        self.logs: list[dict[str, str]] = []
        self.counter = 0
        self.run: dict[str, Any] | None = None
        self.fatal: str | None = None
        self.settings = NeuralSettings().model_dump()
        self.credit_delay = 0.0
        if self.state_file.exists():
            # Never silently replace a corrupt checkpoint with a fresh brain.
            raw = json.loads(self.state_file.read_text())
            if raw.get("lab_schema") == 2:
                if raw["active_model"] not in MODEL_INFO:
                    raise ValueError("Modele actif inconnu dans la sauvegarde.")
                self.models = {key: restore_brain(key, raw["models"][key]) for key in MODEL_INFO}
                self.active_model = raw["active_model"]
                self.settings = NeuralSettings(**raw.get("settings", {})).model_dump()
                self.credit_delay = float(raw.get("credit_delay", 0.0))
                self.counter = int(raw["counter"])
                self.trials = raw["trials"]
                self.log("systeme", "Deux memoires restaurees. Aucun essai interrompu n'est repris.")
            elif "brain" in raw and "lab_schema" not in raw:
                # Alpha 01 -> alpha 02: preserve old weights and RNG exactly.
                self.models["legacy"] = MushroomBody.from_dict(raw["brain"])
                self.counter = int(raw["counter"])
                self.trials = [{**row, "model": "legacy"} for row in raw["trials"]]
                archive = self._archive("migration-alpha01")
                self.save()
                self.log("migration", f"Alpha 01 conservee integralement. Copie : {archive.name}.")
                self.log("migration", "Alpha 02 commence sans apprentissage ; choisir Reference pour retrouver l'ancien reseau.")
            else:
                raise ValueError("Format de laboratoire inconnu ; sauvegarde laissee intacte.")
        else:
            self.log("systeme", "Alpha 02 initialisee (seed 42). Modele synthetique, sans connectome MaleCNS.")
            self.save()

    @property
    def brain(self):
        return self.models[self.active_model]

    @brain.setter
    def brain(self, value):
        self.models[self.active_model] = value

    def log(self, kind: str, message: str) -> None:
        self.logs.append({"time": stamp(), "kind": kind, "message": message})
        self.logs = self.logs[-100:]

    def _archive(self, label: str = "reset") -> Path:
        target = self.data_dir / f"archive-{label}-{time.time_ns()}.json"
        # Exclusive creation and fsync before altering the authoritative state.
        with target.open("xb") as stream:
            stream.write(self.state_file.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        return target

    def checkpoint(self) -> dict[str, Any]:
        return {"lab_schema": 2, "version": VERSION, "active_model": self.active_model,
                "models": {key: brain.to_dict() for key, brain in self.models.items()},
                "counter": self.counter, "trials": self.trials,
                "settings": self.settings, "credit_delay": self.credit_delay,
                "saved_at": stamp()}

    def save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(self.checkpoint(), stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(self.state_file)

    def switch(self, key: str) -> None:
        if key not in MODEL_INFO:
            raise ValueError("Modele inconnu.")
        if self.run and self.run["status"] == "running":
            raise ValueError("Terminer ou interrompre l'essai avant de changer de modele.")
        previous, previous_run = self.active_model, self.run
        self.active_model, self.run = key, None
        try:
            self.save()
        except Exception:
            self.active_model, self.run = previous, previous_run
            raise
        self.log("modele", f"{MODEL_INFO[key]['label']} : memoire et compteurs propres, aucun transfert de poids.")

    def start(self, scenario: Scenario, now: float | None = None) -> None:
        if self.fatal:
            raise RuntimeError(self.fatal)
        if self.run and self.run["status"] == "running":
            raise ValueError("Un essai est deja en cours.")
        old = (self.brain.to_dict(), self.counter, self.run, self.settings, self.credit_delay)
        if self.active_model == "legacy":
            decision = self.brain.decide(scenario.observation(), scenario.epsilon)
        else:
            decision = self.brain.decide(scenario.observation(), scenario.epsilon, scenario.neural.circuit())
        self.counter += 1
        self.settings = scenario.neural.model_dump()
        self.credit_delay = scenario.credit_delay
        self.run = {"id": self.counter, "model": self.active_model, "status": "running",
                    "scenario": scenario, "decision": decision,
                    "start": time.monotonic() if now is None else now,
                    "created_at": stamp(), "result": None}
        try:
            self.save()
        except Exception:
            self.brain = restore_brain(self.active_model, old[0])
            self.counter, self.run, self.settings, self.credit_delay = old[1:]
            raise
        mode = "exploration" if decision.exploratory else "meilleur score / egalite"
        self.log("decision", f"Essai {self.counter} [{self.active_model}] : {ACTIONS[decision.action].upper()} ({mode}).")
        self.log("entree", "Historique, seuil et horizon lus ; futur et nom exclus du reseau.")
        if decision.extra:
            self.log("reseau", f"{np.count_nonzero(decision.kc)}/1024 KC actives ; APL {decision.extra['apl']:.4f} ; "
                     f"{3 if scenario.neural.compartments else 1} compartiment(s).")
            disabled = [k for k, v in self.settings.items() if not v and k != "forgetting"]
            if disabled:
                self.log("ablation", "Desactive : " + ", ".join(disabled) + ".")
        elif scenario.credit_delay or scenario.neural != NeuralSettings():
            self.log("reference", "Reglages alpha 02 ignores par la reference alpha 01.")

    def tick(self, now: float | None = None) -> None:
        if not self.run or self.run["status"] != "running":
            return
        now = time.monotonic() if now is None else now
        if now - self.run["start"] < self.run["scenario"].duration:
            return
        scenario: Scenario = self.run["scenario"]
        decision: Decision = self.run["decision"]
        final_price = scenario.future_prices()[-1]
        outcome = classify(final_price, scenario.price, scenario.neutral_pct)
        reward = 1.0 if outcome == decision.action else -1.0
        before = self.brain.to_dict()
        kwargs = {"credit_delay": scenario.credit_delay} if self.active_model == "compartments" else {}
        result = self.brain.reinforce(decision, reward, scenario.learning_rate,
                                      enabled=scenario.plasticity, **kwargs)
        result.update({"outcome": outcome, "final_price": final_price,
                       "return_pct": (final_price / scenario.price - 1) * 100})
        record = {"id": self.run["id"], "time": stamp(), "model": self.active_model,
                  "scenario": scenario.model_dump(), "chosen": ACTIONS[decision.action],
                  "scores_before": decision.scores.tolist(),
                  "kc_active": np.flatnonzero(decision.kc).tolist(),
                  "features": decision.features.tolist(), "exploratory": decision.exploratory,
                  **result, "outcome": ACTIONS[outcome]}
        self.trials.append(record)
        try:
            self.save()
        except Exception:
            self.brain = restore_brain(self.active_model, before)
            self.trials.pop()
            self.run["status"] = "error"
            self.fatal = "Echec de sauvegarde. Essai annule, poids restaures. Verifier le volume."
            self.log("erreur", self.fatal)
            raise
        self.run["result"] = result
        self.run["status"] = "finished"
        self.log("resultat", f"Resultat : {ACTIONS[outcome].upper()} ; recompense {reward:+.0f}.")
        self.log("plasticite", f"Delta {result['delta']:+.3f} ; variation normalisee {result['weight_change']:.4f}"
                 + ("." if scenario.plasticity else " (poids et oubli geles)."))
        if "compartments" in result:
            for c in result["compartments"]:
                if c["active"]:
                    self.log("memoire", f"{c['name']} : delta {c['delta']:+.3f}, trace {c['eligibility']:.3f}, variation {c['weight_change']:.5f}.")
        self.log("systeme", "Memoire sauvegardee. Prochain essai uniquement sur ta commande.")

    def cancel(self) -> None:
        if not self.run or self.run["status"] != "running":
            raise ValueError("Aucun essai en cours.")
        self.run["status"] = "cancelled"
        self.log("systeme", "Essai interrompu : aucune recompense ni modification des poids.")

    def reset(self, seed: int = 42) -> None:
        if self.run and self.run["status"] == "running":
            raise ValueError("Interrompre l'essai avant de reinitialiser.")
        if self.state_file.exists():
            self._archive("reset")
        old = (self.brain, self.trials, self.run, self.fatal)
        self.brain = MushroomBody(seed) if self.active_model == "legacy" else CompartmentBrain(seed)
        self.trials = [t for t in self.trials if t.get("model", "legacy") != self.active_model]
        self.run, self.fatal = None, None
        try:
            self.save()
        except Exception:
            self.brain, self.trials, self.run, self.fatal = old
            raise
        self.log("systeme", f"{self.active_model} reinitialise (seed {seed}). Autre modele intact ; etat precedent archive.")

    def _stats(self, key: str) -> dict[str, Any]:
        trials = [t for t in self.trials if t.get("model", "legacy") == key]
        total = len(trials)
        hits = sum(t["reward"] > 0 for t in trials)
        return {"trials": total, "hits": hits, "accuracy": hits / total if total else None,
                "reward_sum": sum(t["reward"] for t in trials), "updates": self.models[key].updates}

    def _neural(self, fraction: float = 0.0) -> dict[str, Any]:
        base = {"kc": [0] * MODEL_INFO[self.active_model]["kc"], "mbon_plus": [0] * 3,
                "mbon_minus": [0] * 3, "scores": [0] * 3,
                "dan_plus": 0, "dan_minus": 0, "delta": 0,
                "pn": [], "apl": 0, "inhibition": 0, "compartments": [], "settling": []}
        if not self.run:
            return base
        d: Decision = self.run["decision"]
        result = self.run["result"]
        if d.extra:
            config = CircuitSettings.from_dict(d.extra["config"])
            plus, minus, q = self.brain.outputs(d.kc, config)
            base.update({key: d.extra[key] for key in ("pn", "apl", "inhibition", "settling")})
            base["config"] = d.extra["config"]
            if result:
                base["compartments"] = result["compartments"]
            else:
                _, _, comp_scores = self.brain.compartment_outputs(d.kc)
                for c in range(3):
                    trace = math.exp(-self.run["scenario"].credit_delay * fraction / ELIGIBILITY_TAU[c]) if config.eligibility else 1.0
                    base["compartments"].append({"name": COMPARTMENTS[c], "active": c == 0 or config.compartments,
                        "rate_factor": float(LEARNING_MULTIPLIERS[c]), "delta": 0,
                        "dan_plus": 0, "dan_minus": 0, "scores": comp_scores[c].tolist(),
                        "eligibility": trace, "weight_change": 0})
        else:
            plus, minus, q = self.brain.outputs(d.kc)
        base.update({"kc": d.kc.tolist(), "mbon_plus": plus.tolist(),
                     "mbon_minus": minus.tolist(), "scores": q.tolist(),
                     "dan_plus": result["dan_plus"] if result else 0,
                     "dan_minus": result["dan_minus"] if result else 0,
                     "delta": result["delta"] if result else 0})
        return base

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        own_trials = [t for t in self.trials if t.get("model", "legacy") == self.active_model]
        data: dict[str, Any] = {
            "version": VERSION, "kind": "synthetic-mushroom-body",
            "active_model": self.active_model, "model_info": MODEL_INFO[self.active_model],
            "models": [{**info, "stats": self._stats(key)} for key, info in MODEL_INFO.items()],
            "settings": self.settings, "credit_delay": self.credit_delay,
            "status": self.run["status"] if self.run else "idle", "error": self.fatal,
            "stats": self._stats(self.active_model), "logs": self.logs,
            "recent": own_trials[-8:][::-1], "neural": self._neural(), "run": None}
        if not self.run:
            return data
        s: Scenario = self.run["scenario"]
        d: Decision = self.run["decision"]
        fraction = min(1.0, max(0.0, (now - self.run["start"]) / s.duration))
        if self.run["status"] == "finished":
            fraction = 1.0
        if self.run["status"] in ("cancelled", "error"):
            fraction = 0.0
        path = s.future_prices()
        cursor = fraction * (len(path) - 1)
        i = int(cursor)
        revealed = path[:i+1]
        if i < len(path) - 1 and cursor > i:
            revealed = revealed + [path[i] + (path[i+1] - path[i]) * (cursor - i)]
        data["neural"] = self._neural(fraction)
        data["run"] = {"id": self.run["id"], "name": s.name, "price": s.price,
                       "history": s.history_prices(), "revealed": revealed,
                       "fraction": fraction, "horizon_steps": len(s.future_returns),
                       "neutral_pct": s.neutral_pct, "duration": s.duration,
                       "chosen": d.action, "scores_before": d.scores.tolist(),
                       "exploratory": d.exploratory, "plasticity": s.plasticity,
                       "credit_delay": s.credit_delay, "result": self.run["result"]}
        return data
