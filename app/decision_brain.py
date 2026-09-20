"""Alpha05: same five synthetic motifs, explicit sparsity and optional L2 sensors.
Forecasts are NOT calibrated probabilities. No MaleCNS synapse is imported.
"""
from __future__ import annotations
import copy
import hashlib
import json
import numpy as np
from .motif_brain import MotifBrain, N_KC
from .brain import Decision
from .compartment_brain import CircuitSettings

ENCODER='grid-liquidity-candidate-v3'
N_PN=252  # 24 context features (16 price + 8 L2) + 4 candidate cues


def liquidity_vector(book):
    if not book or not book.get('available'):
        return np.array([-1.,0,0,0,0,0,0,-1.])
    return np.array([1., np.tanh(book['spread']/.5),
        np.tanh(np.log1p(book['bid_depth'])/4), np.tanh(np.log1p(book['ask_depth'])/4),
        np.clip(book['imbalance'],-1,1), np.clip(book['flow_imbalance'],-1,1),
        np.tanh((book['buy_volume_5s']+book['sell_volume_5s'])/10),
        1. if book.get('flow_available') else -1.])


class DecisionBrain(MotifBrain):
    def __init__(self,seed=42,sparsity=.05,use_liquidity=False):
        if not .01<=sparsity<=.5: raise ValueError('Sparsity hors limites')
        super().__init__(seed)
        self.sparsity=float(sparsity); self.use_liquidity=bool(use_liquidity)
        if use_liquidity:
            f=np.stack([np.r_[self.rng.choice(16,4,replace=False),self.rng.integers(16,24),
                              self.rng.integers(24,28)] for _ in range(N_KC)])
        else:
            f=np.stack([np.r_[self.rng.choice(16,5,replace=False),self.rng.integers(24,28)] for _ in range(N_KC)])
        self.indices=f*9+self.rng.integers(0,9,(N_KC,6))
        self.input_weights=self.rng.uniform(.8,1.2,(N_KC,6))
        self.input_weights/=self.input_weights.sum(axis=1,keepdims=True)
        self.book=None;self.visits=np.zeros(N_KC,dtype=np.int64)

    def encode_candidates(self,obs,config):
        context=self.context(obs)
        if not config.temporal: context[[0,1,2,3,4,5,6,9,10,15]]=0
        context=np.r_[context,liquidity_vector(self.book) if self.use_liquidity else np.zeros(8)]
        codes=[];features=[];pns=[];thresholds=[]
        for a in range(3):
            q=np.full(3,-1.);q[a]=1.
            f=np.r_[context,q,np.tanh((1,0,-1)[a]+.5-obs.row_fraction)]
            pn=np.exp(-.5*((f[:,None]-self.centers)/.24)**2).ravel()
            drive=(pn[self.indices]*self.input_weights).sum(axis=1)
            threshold=float(np.quantile(drive,1-self.sparsity)) if config.apl else 0.
            kc=np.clip((drive-threshold)/max(float(drive.max()-threshold),1e-9),0,1)
            kc[kc<1e-6]=0
            codes.append(kc);features.append(f);pns.append(pn);thresholds.append(threshold)
        return np.array(codes),np.array(features),np.array(pns),np.array(thresholds)

    def predict(self,obs,book=None,epsilon=0.,config=None):
        self.book=copy.deepcopy(book)
        d=super().decide(obs,epsilon,config)
        self.visits+=np.any(np.array(d.extra['codes'])>0,axis=0)
        return d

    def learn_outcomes(self,d,touches,rate=.25,signal='all',config=None):
        """Call ONLY after window close. Same pre-outcome activities for every arm.
        Full-information update is a single averaged batch, not 3 ordered updates.
        """
        if signal not in ('all','chosen'): raise ValueError('Signal inconnu')
        if len(touches)!=3 or any(type(v) is not bool for v in touches): raise ValueError('Touches invalides')
        if signal=='chosen': return self.reinforce(d,1. if touches[d.action] else -1.,rate)
        old=self.weights.copy();old_updates=self.updates
        changes=[];results=[]
        for a in range(3):
            self.weights=old.copy();self.updates=old_updates
            dd=Decision(a,False,np.array(d.extra['codes'][a]),d.mbon_plus,d.mbon_minus,
                        d.scores,np.array(d.extra['features_all'][a]),copy.deepcopy(d.extra))
            results.append(self.reinforce(dd,1. if touches[a] else -1.,rate))
            changes.append(self.weights-old)
        self.weights=np.clip(old+np.mean(changes,axis=0),0,1)
        self.updates=old_updates+1
        return {'updated':True,'signal':'all','reward':None,
                'weight_change':float(np.mean(np.abs(self.weights-old))),
                'dan_plus':sum(x['dan_plus'] for x in results)/3,
                'dan_minus':sum(x['dan_minus'] for x in results)/3}

    def neural(self,d,result=None):
        n=super().neural(d,None)  # diagnostics of the fixed current readout
        n.update(kc_ever_active=int(np.count_nonzero(self.visits)),
                 kc_frequency=self.visits.tolist(),sparsity=self.sparsity,
                 encoder=ENCODER,use_liquidity=self.use_liquidity)
        if d:
            c=np.array(d.extra['codes'])>0
            n['candidate_active']=c.sum(axis=1).tolist()
            n['candidate_overlap']=[[float(np.count_nonzero(c[i]&c[j])/max(1,np.count_nonzero(c[i]|c[j])))
                                     for j in range(3)] for i in range(3)]
        if result:
            n['dan_plus']=result.get('dan_plus',0);n['dan_minus']=result.get('dan_minus',0)
        return n

    def to_dict(self):
        d=super().to_dict()
        d.update(kind='synthetic-decision-05',encoder=ENCODER,sparsity=self.sparsity,
                 use_liquidity=self.use_liquidity,visits=self.visits.tolist())
        return d

    @classmethod
    def from_dict(cls,d):
        if d.get('kind')!='synthetic-decision-05' or d.get('encoder')!=ENCODER:
            raise ValueError('Checkpoint alpha05 incompatible')
        m=cls(int(d['seed']),float(d['sparsity']),bool(d['use_liquidity']))
        for key,shape in [('indices',(1024,6)),('input_weights',(1024,6)),('weights',(5,1024)),('visits',(1024,))]:
            v=np.array(d[key])
            if v.shape!=shape or not np.isfinite(v).all() or np.any(v<0): raise ValueError('Poids invalides')
            if key in ('indices','visits') and not np.issubdtype(v.dtype,np.integer): raise ValueError('Entiers attendus')
            if key=='indices' and np.any(v>=N_PN): raise ValueError('Index PN invalide')
            if key in ('weights','input_weights') and np.any(v>1): raise ValueError('Poids hors bornes')
            setattr(m,key,v.copy())
        if not np.allclose(m.input_weights.sum(axis=1),1):raise ValueError('Projection non normalisee')
        m.updates=int(d['updates']);m.reversal=bool(d['reversal']);m.rng.bit_generator.state=d['rng']
        return m

    def fingerprint(self):
        d=self.to_dict()
        for k in ('visits','rng','updates'):d.pop(k,None)
        return hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
