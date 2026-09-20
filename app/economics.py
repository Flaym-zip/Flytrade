"""Explicit paper-only payout assumptions and selective decision gate."""
from __future__ import annotations
import math
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class EconomySettings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    quote_mode: Literal['manual','synthetic']='manual'
    multipliers: list[float]=Field(default_factory=lambda:[3.0,1.30,3.0],min_length=3,max_length=3)
    convention: Literal['total','profit']='total'
    stake: float=Field(default=1.0,gt=0,le=100)
    initial_capital: float=Field(default=100.,ge=1,le=1000000)
    cost_per_stake: float=Field(default=0.,ge=0,le=1)
    haircut: float=Field(default=.01,ge=0,le=.5)
    min_edge: float=Field(default=.05,ge=0,le=2)
    max_drawdown: float=Field(default=20.,gt=0,le=1000000)
    synthetic_margin: float=Field(default=.10,ge=0,le=.5)
    decision_mode: Literal['collect','policy']='collect'

    def check(self):
        if any(not math.isfinite(x) or x<1.01 or x>100 for x in self.multipliers):
            raise ValueError('Multiplicateurs : entre 1.01 et 100.')


def quote_vector(history,reference,start,placed,base_row,settings):
    settings.check()
    if settings.quote_mode=='manual':
        shown=np.array(settings.multipliers,dtype=float)
        gross=shown+(1 if settings.convention=='profit' else 0)
        source='manual_hypothesis'
    else:
        # Hypothetical issuer. Brownian paths from PAST volatility only, no drift.
        # 1s discretization approximates touch and is NOT an Euphoria formula.
        sigma=max(.01,float(np.std(np.diff(np.array(history,dtype=float)))))
        lead=max(10.,start-placed);steps=int(math.ceil(lead+5))
        rng=np.random.default_rng(271828)  # common random numbers: reproducible quotes
        paths=reference+sigma*np.cumsum(rng.normal(size=(256,steps)),axis=1)
        sample=paths[:,max(0,int(math.ceil(lead))-1):steps-1]
        probs=[]
        for shift in (1,0,-1):
            lo=(base_row+shift)*.5
            count=np.any((sample>=lo)&(sample<lo+.5),axis=1).sum()
            probs.append((count+.5)/257.)
        gross=np.clip((1-settings.synthetic_margin)/np.array(probs),1.05,100.)
        shown=gross.copy();source='synthetic_brownian_issuer_NOT_EUPHORIA'
    effective=gross*(1-settings.haircut)
    return {'displayed':shown.tolist(),'gross':gross.tolist(),'effective_gross':effective.tolist(),
            'source':source,'convention':settings.convention if source.startswith('manual') else 'total',
            'haircut':settings.haircut,'cost_per_stake':settings.cost_per_stake,
            'asof':float(placed),'synthetic_margin':settings.synthetic_margin if settings.quote_mode=='synthetic' else None}


def payoff(hit,gross,stake,cost_per_stake=0.):
    """Net of initial stake and explicit paper cost. Includes stake on a win."""
    return stake*((gross if hit else 0.)-1.-cost_per_stake)


def policy(probabilities,quote,settings,capital,drawdown,position_open=False,feature_ready=True):
    rows=[]
    for a in range(3):
        p=probabilities[a]
        m=quote['effective_gross'][a]
        rows.append({'p':p.get('p'),'lower':p.get('lower'),'n':p.get('n',0),'ready':p.get('ready',False),
                     'break_even':(1+settings.cost_per_stake)/m,
                     'ev':p['p']*m-1-settings.cost_per_stake if p.get('p') is not None else None,
                     'ev_lower':p['lower']*m-1-settings.cost_per_stake if p.get('lower') is not None else None})
    reason=None
    if settings.decision_mode=='collect':reason='Collecte sans mise (reglage)'
    elif position_open:reason='Une position simulee est deja ouverte'
    elif capital<settings.stake*(1+settings.cost_per_stake):reason='Capital fictif insuffisant'
    elif drawdown>=settings.max_drawdown:reason='Limite de perte fictive atteinte'
    elif not feature_ready:reason='Entrees de liquidite manquantes ou perimees'
    valid=[a for a,r in enumerate(rows) if r['ready'] and r['ev_lower'] is not None and r['ev_lower']>settings.min_edge]
    if reason is None and not valid:
        reason='Calibration insuffisante' if not any(r['ready'] for r in rows) else 'Aucune case avec avantage prudent suffisant'
    chosen=max(valid,key=lambda a:rows[a]['ev_lower']) if reason is None else None
    return {'chosen':chosen,'action':('hausse','stable','baisse')[chosen] if chosen is not None else 'attendre',
            'reason':reason or 'Avantage estime superieur au seuil (aucune garantie)', 'candidates':rows}


class Calibrator:
    """Per-event binned isotonic frequencies on a SEPARATE chronological block.
    Wilson lower limits are a heuristic guard (temporal dependence not corrected).
    No softmax: three touch events are not mutually exclusive.
    """
    def __init__(self, fingerprint='', min_total=30, min_bin=10):
        self.fingerprint=fingerprint;self.min_total=min_total;self.min_bin=min_bin
        self.actions=[[],[],[]];self.n=0

    def fit(self,scores,labels,fingerprint):
        q=np.array(scores,dtype=float); y=np.array(labels,dtype=bool)
        if q.ndim!=2 or q.shape[1]!=3 or q.shape!=y.shape or not np.isfinite(q).all():raise ValueError('Calibration invalide')
        self.n=len(q);self.fingerprint=fingerprint;self.actions=[]
        for a in range(3):
            order=np.argsort(q[:,a],kind='stable');xs=q[order,a];ys=y[order,a]
            # Quantile edges never split equal scores. Empty bins removed.
            cuts=np.unique(np.quantile(xs,[.25,.5,.75])) if len(xs) else []
            groups=[]
            idx=np.searchsorted(cuts,xs,side='right')
            for k in np.unique(idx):
                mask=idx==k
                groups.append({'min':float(xs[mask].min()),'max':float(xs[mask].max()),
                               'n':int(mask.sum()),'hits':int(ys[mask].sum())})
                while len(groups)>1 and groups[-2]['hits']/groups[-2]['n']>groups[-1]['hits']/groups[-1]['n']:
                    b=groups.pop();c=groups.pop()
                    groups.append({'min':c['min'],'max':b['max'],'n':c['n']+b['n'],'hits':c['hits']+b['hits']})
            self.actions.append(groups)
        return self

    def predict(self,scores,fingerprint):
        out=[]
        for a,score in enumerate(scores):
            groups=self.actions[a]
            if fingerprint!=self.fingerprint or not groups:
                out.append({'p':None,'lower':None,'n':0,'ready':False});continue
            mids=[(groups[i]['max']+groups[i+1]['min'])/2 for i in range(len(groups)-1)]
            g=groups[int(np.searchsorted(mids,score,side='right'))]
            n=g['n'];p=g['hits']/n;z=1.96
            lower=(p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
            # Refuse far extrapolation beyond observed score support.
            support=groups[0]['min']-.10<=score<=groups[-1]['max']+.10
            out.append({'p':p,'lower':max(0.,lower),'n':n,
                        'ready':bool(self.n>=self.min_total and n>=self.min_bin and support)})
        return out

    def to_dict(self):return dict(fingerprint=self.fingerprint,min_total=self.min_total,min_bin=self.min_bin,actions=self.actions,n=self.n)
    @classmethod
    def from_dict(cls,d):
        if not isinstance(d,dict) or set(d)!={'fingerprint','min_total','min_bin','actions','n'}:
            raise ValueError('Schema de calibration invalide')
        fingerprint=d['fingerprint'];n=d['n'];actions=d['actions']
        if type(n) is not int or n<0 or type(d['min_total']) is not int or d['min_total']<=0 or type(d['min_bin']) is not int or d['min_bin']<=0:
            raise ValueError('Compteurs de calibration invalides')
        if not isinstance(fingerprint,str) or (n and (len(fingerprint)!=64 or any(x not in '0123456789abcdef' for x in fingerprint))):
            raise ValueError('Empreinte de calibration invalide')
        if n==0 and fingerprint not in ('',):raise ValueError('Calibration vide avec empreinte inattendue')
        if not isinstance(actions,list) or len(actions)!=3:raise ValueError('Trois groupes de calibration attendus')
        clean=[]
        for groups in actions:
            if not isinstance(groups,list) or (n>0 and not groups) or (n==0 and groups):
                raise ValueError('Groupes de calibration incoherents')
            previous=None;total=0;out=[]
            for g in groups:
                if not isinstance(g,dict) or set(g)!={'min','max','n','hits'}:raise ValueError('Groupe de calibration invalide')
                lo,hi=float(g['min']),float(g['max']);count,hits=g['n'],g['hits']
                if not math.isfinite(lo) or not math.isfinite(hi) or lo>hi:raise ValueError('Bornes de calibration invalides')
                if type(count) is not int or count<=0 or type(hits) is not int or not 0<=hits<=count:raise ValueError('Effectifs de calibration invalides')
                if previous is not None and lo<previous:raise ValueError('Groupes de calibration non ordonnes')
                previous=hi;total+=count;out.append({'min':lo,'max':hi,'n':count,'hits':hits})
            if total!=n:raise ValueError('Total de calibration incoherent')
            clean.append(out)
        c=cls(fingerprint,d['min_total'],d['min_bin']);c.actions=clean;c.n=n;return c
