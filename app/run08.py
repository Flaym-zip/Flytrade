"""User-visible lifecycle operations. No change to price or risk algorithms."""
import copy
import json
import time
from .run06 import Run06
from .brain06 import Brain06
from .economics import Calibrator

class Run08(Run06):
    def log(self, kind, message):
        return super().log(kind, message.replace('Alpha07', 'Alpha08'))

    def checkpoint(self):
        d=super().checkpoint();d['version']='0.8.0-alpha';return d

    def snapshot(self, now=None):
        d=super().snapshot(now);d['version']='0.8.0-alpha'
        d['brain_id']=self.brain.fingerprint()[:12]
        d['brain_config']=dict(seed=self.brain.seed,n_kc=self.brain.n_kc,
            sparsity=self.brain.sparsity,use_liquidity=self.brain.use_liquidity)
        return d

    def reset_brain_only(self, config):
        if self.collect or self.policy_enabled or self.pending:
            raise ValueError('Arreter collecte et mises, puis attendre la fin des fenetres avant de remplacer le cerveau du marche.')
        old=copy.deepcopy(self.checkpoint())
        self.brain=Brain06(seed=config.seed,n_kc=config.n_kc,sparsity=config.sparsity,use_liquidity=config.use_liquidity)
        self.calibrator=Calibrator(min_total=60,min_bin=20)
        self.deployment=None;self.last=None;self.last_decision=None;self.last_financial=None
        try:
            with self.db:
                self.db.execute('INSERT INTO archives VALUES (?,?)',(time.time_ns(),json.dumps(old,allow_nan=False)))
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps(self.checkpoint(),allow_nan=False),))
        except Exception:self.restore(old);raise
        self.log('cerveau','Cerveau du marche vierge; portefeuille et observations conserves. Calibration vide : mises bloquees.')
