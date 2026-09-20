"""Artificial associative reversal, never reported as an ETH market experiment."""
import numpy as np
from .live import GridObservation
from .motif_brain import MotifBrain
from .compartment_brain import CircuitSettings


def reversal_check(seed=42):
    brains = {'revision_active': MotifBrain(seed), 'sans_revision': MotifBrain(seed)}
    brains['sans_revision'].reversal = False
    histories = [tuple(3000+np.linspace(-1, 0, 31)), tuple([3000.]*31),
                 tuple(3000+np.linspace(1, 0, 31))]
    rows = []
    for i in range(240):
        category = i % 3
        good = category if i < 120 else (2, 1, 0)[category]
        obs = GridObservation(histories[category], .5/3000*100, 17, 0.)
        row = {'trial': i+1, 'phase': 'association' if i < 120 else 'inversion', 'stimulus': category}
        for name, brain in brains.items():
            d = brain.decide(obs, .15, CircuitSettings())
            reward = 1. if d.action == good else -1.
            brain.reinforce(d, reward, .25)
            row[name] = int(reward > 0)
        rows.append(row)
    return {'source': 'SYNTHETIQUE / diagnostic, aucun prix de marche',
            'seed': seed, 'rows': rows, 'inversion_at': 121,
            'blocks': [{'first': start+1, 'last': start+30,
                       **{k: sum(r[k] for r in rows[start:start+30])/30 for k in brains}}
                       for start in range(0,240,30)],
            'note': 'Motifs abstraits et permutations artificielles de recompense. Pas de conclusion sur ETH ni MaleCNS.'}
