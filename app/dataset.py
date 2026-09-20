"""Auditable replay windows. Future ticks belong to the environment, never brain input."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from typing import Any
import numpy as np
from .live import GridObservation, row_of

MAX_BYTES = 25*1024*1024
MAX_EPISODES = 5000


def canonical(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(x: Any) -> str:
    return hashlib.sha256(canonical(x).encode()).hexdigest()


@dataclass(frozen=True)
class Episode:
    uid: str
    source: str
    placed: float
    start: float
    end: float
    reference: float
    base_row: int
    history_end: float
    history: tuple[float, ...]
    ticks: tuple[tuple[float, float, int], ...]
    touches: tuple[bool, bool, bool]

    @property
    def support_start(self) -> float:
        return self.history_end - len(self.history) + 1

    @property
    def category(self) -> str:
        n = sum(self.touches)
        return ('hausse', 'stable', 'baisse')[self.touches.index(True)] if n == 1 else 'multiple' if n else 'aucune'

    def observation(self) -> GridObservation:
        # New immutable object exposes only quantities available at placement.
        frac = (self.reference - .5*self.base_row)/.5
        return GridObservation(self.history, .5/self.reference*100,
                               int(math.ceil(self.end-self.placed)), frac, self.reference)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, x: dict) -> 'Episode':
        d = dict(x)
        d['history'] = tuple(d['history']); d['ticks'] = tuple(tuple(t) for t in d['ticks'])
        d['touches'] = tuple(d['touches'])
        return cls(**d)


def normalize(row: dict, lead_max: float = 15.) -> Episode:
    if row.get('status') not in ('won', 'lost'):
        raise ValueError('annule_ou_inacheve')
    names = ('placed', 'start', 'end', 'reference', 'history_end')
    vals = [float(row[n]) for n in names]
    if not all(math.isfinite(x) for x in vals):
        raise ValueError('valeur_non_finie')
    placed, start, end, ref, hend = vals
    if ref <= 0 or end-start != 5 or start-placed < 10-1e-6 or start-placed >= lead_max+1e-6:
        raise ValueError('contrat_incompatible')
    if abs(start/5-round(start/5)) > 1e-6:
        raise ValueError('grille_non_alignee')
    history = tuple(float(v) for v in row['history'])
    if len(history) != 31 or not all(math.isfinite(v) and v > 0 for v in history):
        raise ValueError('historique_incompatible')
    # Legacy history ends on the floor(watermark) sample, known at decision.
    if hend > placed or placed-hend > 4:
        raise ValueError('historique_non_causal')
    base = row_of(ref)
    if row.get('base_row') != base:
        raise ValueError('borne_de_prix_incoherente')
    ticks = []
    ids = set()
    for t in row.get('window_ticks', []):
        if len(t) != 3 or type(t[2]) is not int:
            raise ValueError('transaction_invalide')
        when, price, tid = float(t[0]), float(t[1]), t[2]
        if not math.isfinite(when) or not math.isfinite(price) or price <= 0 or tid < 0:
            raise ValueError('transaction_invalide')
        if not start <= when < end:
            raise ValueError('transaction_hors_fenetre')
        if ticks and (when < ticks[-1][0] or tid <= ticks[-1][2]):
            raise ValueError('transactions_non_ordonnees')
        if tid in ids: raise ValueError('transaction_dupliquee')
        ids.add(tid); ticks.append((when, price, tid))
    if not ticks: raise ValueError('aucune_transaction')
    if len(ticks) > 20000: raise ValueError('trop_de_transactions')
    touches = tuple(any(row_of(p) == base+s for _, p, _ in ticks) for s in (1, 0, -1))
    supplied = row.get('touches')
    if supplied is not None and (len(supplied) != 3 or any(type(x) is not bool for x in supplied) or tuple(supplied) != touches):
        raise ValueError('touches_incoherentes')
    if type(row.get('chosen')) is not int or row['chosen'] not in (0, 1, 2):
        raise ValueError('choix_invalide')
    if (row['status'] == 'won') != touches[row['chosen']]:
        raise ValueError('resultat_incoherent')
    source = str(row.get('source', 'source_non_precisee'))[:100]
    identity = sha({'source': source, 'placed': placed, 'start': start, 'reference': ref})
    return Episode(identity, source, placed, start, end, ref, base, hend, history,
                   tuple(ticks), touches)


def parse_jsonl(raw: bytes) -> tuple[list[Episode], dict]:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('Fichier vide ou superieur a 25 Mio.')
    text = raw.decode('utf-8-sig')
    rows = text.splitlines()
    if len(rows) > MAX_EPISODES:
        raise ValueError('Maximum 5000 fenetres par import.')
    episodes, rejected, seen = [], Counter(), {}
    for number, line in enumerate(rows, 1):
        if not line.strip(): continue
        try:
            row = json.loads(line, parse_constant=lambda s: (_ for _ in ()).throw(ValueError('non_finite')))
            if not isinstance(row, dict): raise ValueError('objet_attendu')
            ep = normalize(row)
            if ep.uid in seen:
                if seen[ep.uid] != ep:
                    raise ValueError('identite_contradictoire')
                rejected['doublon'] += 1
                continue
            seen[ep.uid] = ep
            episodes.append(ep)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            label = str(exc) if isinstance(exc, ValueError) and len(str(exc)) < 65 else 'schema_invalide'
            rejected[label] += 1
    if not episodes:
        raise ValueError('Aucune fenetre exploitable. Rejets : '+canonical(dict(rejected)))
    episodes.sort(key=lambda e: (e.placed, e.uid))
    return episodes, {'import_sha256': hashlib.sha256(raw).hexdigest(),
                      'input_lines': len(rows), 'accepted': len(episodes),
                      'rejected': dict(rejected)}


def distribution(episodes: list[Episode]) -> dict:
    counts = Counter(e.category for e in episodes)
    n = len(episodes)
    return {'n': n, 'categories': {c: counts[c] for c in ('hausse', 'stable', 'baisse', 'multiple', 'aucune')},
        'touch_rates': [sum(e.touches[i] for e in episodes)/n if n else 0 for i in range(3)],
        'first': episodes[0].placed if n else None, 'last': episodes[-1].end if n else None}


def split_chronological(episodes: list[Episode]) -> tuple[dict[str, list[Episode]], dict]:
    """Split BEFORE outcome selection; purge prior split until full supports disjoint.

    Five extra seconds of embargo between complete [history_start, target_end)
    intervals. This also handles overlapping replay windows.
    """
    data = sorted(episodes, key=lambda e: (e.placed, e.uid))
    n = len(data)
    if n < 15: raise ValueError('Au moins 15 fenetres valides pour un decoupage chronologique.')
    a, b = int(n*.70), int(n*.85)
    train, validation, test = data[:a], data[a:b], data[b:]
    val_cut = min(e.support_start for e in validation)
    test_cut = min(e.support_start for e in test)
    train2 = [e for e in train if e.end+5 <= val_cut]
    val2 = [e for e in validation if e.end+5 <= test_cut]
    if min(len(train2), len(val2), len(test)) < 3:
        raise ValueError('Trop peu de fenetres non chevauchantes apres purge. Collecter davantage.')
    splits = {'train': train2, 'validation': val2, 'test': test}
    manifest = {key: [e.uid for e in rows] for key, rows in splits.items()}
    report = {'fingerprint': sha([e.to_dict() for e in data]),
              'splits': {k: distribution(v) for k, v in splits.items()},
              'purged': n-sum(len(v) for v in splits.values()), 'embargo_seconds': 5,
              'manifest_sha256': sha(manifest), 'manifest': manifest}
    return splits, report


def curriculum(splits: dict[str, list[Episode]], epochs: int, seed: int) -> list[tuple[str, Episode]]:
    if not 1 <= epochs <= 30: raise ValueError('Entre 1 et 30 passages equilibres.')
    train = splits['train']
    pools = [[e for e in train if e.category == c] for c in ('hausse', 'stable', 'baisse')]
    k = min(map(len, pools))
    if not k:
        raise ValueError('Une classe exclusive manque dans TRAIN : collecter davantage ou utiliser le mode chronologique.')
    rng = np.random.default_rng(seed)
    out = []
    for epoch in range(epochs):
        selected = [pool[int(i)] for pool in pools for i in rng.choice(len(pool), k, replace=False)]
        # Balancing selects by outcome for the environment, not for the agent.
        # Chronological within each pass; restarting a pass is explicit replay.
        out.extend((f'A / passage {epoch+1}', e) for e in sorted(selected, key=lambda e: e.placed))
    out.extend(('B / multiples et aucune', e) for e in train if sum(e.touches) != 1)
    out.extend(('C / naturel chronologique', e) for e in train)
    out.extend(('validation / gelee', e) for e in splits['validation'])
    if len(out) > 15000: raise ValueError('Protocole trop long : reduire le nombre de passages.')
    return out
