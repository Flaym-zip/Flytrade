"""Paper-only, fixed-cell touch experiments on an observed ETH-USD feed.

The old scenario memories are never changed. SQLite atomically records live
weights, random state and trial outcome. Only the chosen cell is reinforced.
"""
from __future__ import annotations

import copy
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .brain import ACTIONS, Decision, Observation
from .compartment_brain import COMPARTMENTS, CompartmentBrain, CircuitSettings
from .engine import NeuralSettings, stamp
from .market import Market, Tick

CELL_SECONDS = 5
CELL_USD = 0.5
MIN_LEAD = 10
ENCODER = "grid-usd-0.5-v1"
MAX_TRIAL_TICKS = 20000


def row_of(price: float) -> int:
    return int((Decimal(str(price))/Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


def first_window(now: float) -> float:
    # Grid lines are Unix-time multiples of 5. Never round the lead DOWN.
    return float(math.ceil((now + MIN_LEAD)/CELL_SECONDS)*CELL_SECONDS)


@dataclass(frozen=True)
class GridObservation(Observation):
    row_fraction: float = 0.5
    reference_price: float | None = None


class GridBrain(CompartmentBrain):
    @staticmethod
    def features(obs: GridObservation, temporal: bool = True) -> np.ndarray:
        p = np.asarray(obs.history, dtype=float)
        if len(p) < 4 or not np.all(np.isfinite(p)) or np.any(p <= 0):
            raise ValueError("Historique de marche invalide.")
        r = np.diff(p)/CELL_USD
        fast = slow = float(r[0])
        for value in r[1:]:
            fast = 0.6*value + 0.4*fast
            slow = 0.15*value + 0.85*slow
        raw = [r[-1], r[-3:].mean(), r[-10:].mean(), r[-10:].std(),
               r[-1]-r[-2], (p[-1]-p[0])/(CELL_USD*np.sqrt(len(r))),
               2*obs.row_fraction-1, (obs.horizon_steps-15)/5,
               fast, slow, fast-slow, r[-6:-3].mean()]
        features = np.tanh(raw)
        if not temporal:
            features[8:] = 0
        return features


class LiveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    epsilon: float = Field(default=0.10, ge=0, le=1)
    learning_rate: float = Field(default=0.25, gt=0, le=1)
    plasticity: StrictBool = True
    neural: NeuralSettings = Field(default_factory=NeuralSettings)
    credit_delay: float = Field(default=0, ge=0, le=240)


def empty_stats() -> dict[str, Any]:
    return {"completed": 0, "won": 0, "lost": 0, "void": 0, "points": 0,
            "opportunities": [0, 0, 0], "chosen": [0, 0, 0], "wins_chosen": [0, 0, 0]}


class Live:
    brain_class = GridBrain
    encoder = ENCODER
    database_name = "flytrade-live.sqlite3"
    model_key = "reference"
    compartment_names = COMPARTMENTS
    def __init__(self, data_dir: Path, market: Market) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.market = market
        self.db = sqlite3.connect(data_dir / self.database_name)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        """)
        self.brain = self.brain_class(42)
        self.settings = LiveSettings()
        self.counter = 0
        self.generation = 1
        self.stats = empty_stats()
        self.auto = False
        self.next_auto = 0.0
        self.run: dict[str, Any] | None = None
        self.logs: list[dict[str, str]] = []
        self.fatal: str | None = None
        raw = self.db.execute("SELECT payload FROM checkpoint WHERE id=1").fetchone()
        if raw:
            value = json.loads(raw[0])
            if value.get("schema") != 1 or value.get("encoder") != self.encoder:
                raise ValueError("Memoire live incompatible ; fichier conserve.")
            self.brain = self.brain_class.from_dict(value["brain"])
            self.settings = LiveSettings(**value["settings"])
            self.counter = value["counter"]
            self.generation = value["generation"]
            self.stats = value["stats"]
            pending = value.get("pending")
            if pending:
                pending.update(status="void", reason="Redemarrage : observation interrompue.",
                               reward=None, result=None, finished_at=stamp())
                self.stats["void"] += 1
                self._save(record=pending)
                self.log("securite", "Essai interrompu annule, sans apprentissage. Mode auto desarme.")
            self.log("memoire", f"Memoire ETH restauree : {self.brain.updates} mises a jour. Scenarios independants.")
        else:
            self._save()
            self.log("memoire", "Nouvelle memoire ETH (seed 42). Les deux anciens reseaux restent intacts.")

    def close(self) -> None:
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.close()

    def log(self, kind: str, message: str) -> None:
        self.logs.append({"time": stamp(), "kind": kind, "message": message})
        self.logs = self.logs[-80:]

    @property
    def running(self) -> bool:
        return self.run is not None and self.run["public"]["status"] == "pending"

    def checkpoint(self) -> dict[str, Any]:
        return {"schema": 1, "encoder": self.encoder, "version": "0.5.0-alpha",
                "brain": self.brain.to_dict(), "settings": self.settings.model_dump(),
                "counter": self.counter, "generation": self.generation,
                "stats": self.stats,
                "pending": self.run["public"] if self.running else None,
                "saved_at": stamp()}

    def _save(self, record: dict[str, Any] | None = None) -> None:
        raw = json.dumps(self.checkpoint(), allow_nan=False, separators=(",", ":"))
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT OR REPLACE INTO checkpoint(id,payload) VALUES(1,?)", (raw,))
            if record:
                self.db.execute("INSERT INTO trades(id,payload) VALUES(?,?)",
                                (record["id"], json.dumps(record, allow_nan=False, separators=(",", ":"))))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def configure(self, config: LiveSettings) -> None:
        if self.running:
            raise ValueError("Reglages verrouilles pendant un essai.")
        before = self.settings
        self.settings = config
        try:
            self._save()
        except Exception:
            self.settings = before
            raise

    def start(self, now: float | None = None) -> None:
        if self.fatal:
            raise ValueError(self.fatal)
        if self.running:
            raise ValueError("Un seul essai peut etre ouvert a la fois.")
        reason = self.market.ready_reason(now)
        if reason:
            raise ValueError(reason)
        history = self.market.history()
        if history is None:
            raise ValueError("Historique insuffisant.")
        price = self.market.ticks[-1].price
        base_row = row_of(price)
        row_fraction = (price-base_row*CELL_USD)/CELL_USD
        decision_time = self.market.estimated_time(now)
        proposal = first_window(decision_time)
        observation = GridObservation(history, CELL_USD/price*100, int(math.ceil(proposal+5-decision_time)), row_fraction, price)
        old = (self.brain.to_dict(), self.counter, self.run)
        decision = self.brain.decide(observation, self.settings.epsilon, self.settings.neural.circuit())
        # Include actual inference time when checking the 10-second cutoff.
        placed = self.market.estimated_time(now)
        target_start = first_window(placed)
        if target_start != proposal:
            # Recompute with the real observable horizon, not a changed future.
            self.brain = self.brain_class.from_dict(old[0])
            observation = GridObservation(history, CELL_USD/price*100,
                                          int(math.ceil(target_start+5-placed)), row_fraction, price)
            decision = self.brain.decide(observation, self.settings.epsilon, self.settings.neural.circuit())
            placed = self.market.estimated_time(now)
            if target_start-placed < MIN_LEAD:
                self.brain = self.brain_class.from_dict(old[0])
                raise ValueError("Limite de placement depassee pendant le calcul ; relancer.")
        target_row = base_row + (1, 0, -1)[decision.action]
        self.counter += 1
        public = {"id": self.counter, "generation": self.generation, "seed": self.brain.seed, "source": "coinbase-ETH-USD",
                  "created_at": stamp(), "status": "pending", "placed": placed,
                  "start": target_start, "end": target_start+CELL_SECONDS,
                  "deadline": target_start-MIN_LEAD,
                  "reference": price, "base_row": base_row, "target_row": target_row,
                  "lower": target_row*CELL_USD, "upper": (target_row+1)*CELL_USD,
                  "chosen": decision.action, "action": ACTIONS[decision.action],
                  "scores_before": decision.scores.tolist(), "exploratory": decision.exploratory,
                  "history": list(history), "history_end": math.floor(self.market.watermark),
                  "features": decision.features.tolist(), "row_fraction": row_fraction,
                  "settings": self.settings.model_dump(), "encoder": self.encoder,
                  "hit": False, "hit_time": None, "hit_price": None, "hit_trade_id": None,
                  "touches": [False, False, False], "observed": 0, "reward": None,
                  "result": None, "reason": None}
        self.run = {"public": public, "decision": decision, "epoch": self.market.epoch,
                    "ticks": [], "result": None}
        try:
            self._save()
        except Exception:
            self.brain = self.brain_class.from_dict(old[0])
            self.counter, self.run = old[1:]
            raise
        self.log("choix", f"#{self.counter} {ACTIONS[decision.action].upper()} : [{public['lower']:.2f}, {public['upper']:.2f}[ USD ; preavis {target_start-placed:.2f} s.")
        self.log("neurones", f"{np.count_nonzero(decision.kc)}/1024 KC actives. Choix fige ; seul le passe est fourni.")

    def observe(self, tick: Tick) -> None:
        if not self.running:
            return
        p = self.run["public"]
        if tick.epoch != self.run["epoch"]:
            self.invalidate("Changement de session de donnees.")
            return
        if not p["start"] <= tick.t < p["end"]:
            return
        if len(self.run["ticks"]) >= MAX_TRIAL_TICKS:
            self.invalidate("Volume de transactions trop important pour cet enregistrement.")
            return
        self.run["ticks"].append([tick.t, tick.price, tick.trade_id])
        p["observed"] += 1
        tick_row = row_of(tick.price)
        for action, shift in enumerate((1, 0, -1)):
            if tick_row == p["base_row"]+shift:
                p["touches"][action] = True
        if tick_row == p["target_row"] and not p["hit"]:
            p.update(hit=True, hit_time=tick.t, hit_price=tick.price, hit_trade_id=tick.trade_id)
            self.log("touche", f"#{p['id']} : case touchee a {tick.price:.2f} USD. Validation a la fin de la fenetre.")

    def tick(self, now: float | None = None) -> None:
        if self.fatal:
            return
        if not self.market.transport_healthy(now):
            if self.running or self.auto:
                self.invalidate("Flux absent ou en retard (> 3 s).")
            return
        if self.running:
            p = self.run["public"]
            if self.run["epoch"] != self.market.epoch:
                self.invalidate("Discontinuite du flux.")
            elif self.market.watermark >= p["end"]+0.5:
                if p["observed"] == 0:
                    self.invalidate("Aucune transaction observee dans la fenetre de 5 s.")
                else:
                    self.finish()
                    self.next_auto = self.market.estimated_time(now)+2
        elif self.auto and self.market.estimated_time(now) >= self.next_auto:
            if self.market.ready_reason(now) is None:
                self.start(now)

    def finish(self) -> None:
        if not self.running:
            return
        p = self.run["public"]
        before = (self.brain.to_dict(), copy.deepcopy(self.stats), copy.deepcopy(p))
        reward = 1.0 if p["hit"] else -1.0
        settings = LiveSettings(**p["settings"])
        result = self.brain.reinforce(self.run["decision"], reward, settings.learning_rate,
                                      enabled=settings.plasticity, credit_delay=settings.credit_delay)
        p.update(status="won" if p["hit"] else "lost", reward=reward,
                 result=result, finished_at=stamp())
        self.stats["completed"] += 1
        self.stats["won" if p["hit"] else "lost"] += 1
        self.stats["points"] += int(reward)
        a = p["chosen"]
        self.stats["chosen"][a] += 1
        self.stats["wins_chosen"][a] += int(p["hit"])
        for i in range(3):
            self.stats["opportunities"][i] += int(p["touches"][i])
        record = {**p, "window_ticks": self.run["ticks"]}
        try:
            self._save(record)
        except Exception:
            self.brain = self.brain_class.from_dict(before[0])
            self.stats = before[1]
            self.run["public"] = before[2]
            self.fatal = "Sauvegarde impossible : poids restaures. Redemarrer apres verification du stockage."
            self.auto = False
            self.log("erreur", self.fatal)
            raise
        self.run["result"] = result
        self.log("resultat", f"#{p['id']} {p['status'].upper()} : {reward:+.0f} point ; {p['observed']} transactions verifiees.")
        self.log("plasticite", f"DAN+ {result['dan_plus']:.3f} / DAN- {result['dan_minus']:.3f} ; modification {result['weight_change']:.5f}" + ("." if settings.plasticity else " ; poids geles."))

    def invalidate(self, reason: str) -> None:
        was_auto = self.auto
        self.auto = False
        if self.running:
            p = self.run["public"]
            before = copy.deepcopy(p)
            self.stats["void"] += 1
            p.update(status="void", reason=reason, reward=None, finished_at=stamp())
            try:
                self._save({**p, "window_ticks": self.run["ticks"]})
            except Exception:
                self.stats["void"] -= 1
                self.run["public"] = before
                self.fatal = "Echec d'archivage d'un essai annule ; redemarrage requis."
                raise
            self.log("annulation", f"#{p['id']} : {reason} Aucun point, aucun apprentissage.")
        if was_auto:
            self.log("securite", "Mode automatique desarme ; reactivation manuelle necessaire.")

    def set_auto(self, enabled: bool, now: float | None = None) -> None:
        if enabled and self.fatal:
            raise ValueError(self.fatal)
        if enabled:
            reason = self.market.ready_reason(now)
            if reason:
                raise ValueError(reason)
        self.auto = enabled
        self.log("controle", "Mode auto active : un essai a la fois, puis pause de 2 s." if enabled else "Mode auto arrete. L'essai deja place se termine normalement.")

    def reset(self, seed: int = 42) -> None:
        if self.running or self.auto:
            raise ValueError("Arreter le mode auto et terminer l'essai avant de reinitialiser.")
        before = (self.brain, self.stats, self.generation, self.run)
        self.brain, self.stats = self.brain_class(seed), empty_stats()
        self.generation += 1
        self.run = None
        try:
            self._save()
        except Exception:
            self.brain, self.stats, self.generation, self.run = before
            raise
        self.log("memoire", "Memoire ETH reinitialisee. Anciennes generations conservees dans l'export ; scenarios intacts.")

    def recent(self, n: int = 12) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT payload FROM trades ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        records = []
        for raw, in rows:
            p = json.loads(raw)
            records.append({k: p.get(k) for k in ("id", "action", "status", "reward", "lower", "upper", "placed", "start", "end", "observed", "reason", "generation")})
        return records

    def neural(self) -> dict[str, Any]:
        if hasattr(self.brain, "neural"):
            return self.brain.neural(self.run["decision"] if self.run else None, self.run["result"] if self.run else None)
        out = {"kc": [], "pn": [], "apl": 0, "mbon_plus": [0]*3, "mbon_minus": [0]*3,
               "scores": [0]*3, "dan_plus": 0, "dan_minus": 0, "compartments": []}
        if not self.run:
            return out
        d: Decision = self.run["decision"]
        config = CircuitSettings.from_dict(d.extra["config"])
        plus, minus, scores = self.brain.outputs(d.kc, config)
        result = self.run["result"]
        out.update(kc=d.kc.tolist(), pn=d.extra["pn"], apl=d.extra["apl"],
                   mbon_plus=plus.tolist(), mbon_minus=minus.tolist(), scores=scores.tolist())
        if result:
            out.update(dan_plus=result["dan_plus"], dan_minus=result["dan_minus"], compartments=result["compartments"])
        else:
            _, _, cs = self.brain.compartment_outputs(d.kc)
            out["compartments"] = [{"name": name, "active": c == 0 or config.compartments,
                                     "scores": cs[c].tolist(), "dan_plus": 0, "dan_minus": 0,
                                     "delta": 0, "weight_change": 0} for c, name in enumerate(self.compartment_names)]
        return out

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        market = self.market.snapshot(now)
        reference = market["price"]
        now_exchange = market["exchange_time"]
        preview = None
        if reference is not None and now_exchange:
            base = row_of(reference)
            start = first_window(now_exchange)
            preview = {"base_row": base, "start": start, "end": start+5,
                       "deadline": start-10, "reference": reference}
        return {"version": "0.5.0-alpha", "model_key": self.model_key, "market": market, "auto": self.auto,
                "running": self.running, "error": self.fatal,
                "grid": {"seconds": 5, "usd": 0.5, "lead": 10, "rule": "observed-touch"},
                "preview": preview, "trade": self.run["public"] if self.run else None,
                "stats": {**self.stats, "updates": self.brain.updates},
                "settings": self.settings.model_dump(), "neural": self.neural(),
                "logs": self.logs, "recent": self.recent(), "generation": self.generation}
