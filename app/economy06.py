"""Paper EUR wallet. ETH grid remains USD. No FX conversion, no real orders.
Quotes are hypotheses, NEVER Euphoria prices. Delayed fills use fixed contracts.
"""
from __future__ import annotations
import math
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .economics import Calibrator

STAKES = (.1, 1., 5., 10., 20.)

class Rules06(BaseModel):
    model_config=ConfigDict(extra='forbid', allow_inf_nan=False)
    quote_mode: Literal['manual','synthetic']='synthetic'
    multipliers: list[float]=Field(default_factory=lambda:[3.,1.3,3.], min_length=3, max_length=3)
    convention: Literal['total','profit']='total'
    execution_delay: float=Field(default=2., ge=0, le=5)
    max_slippage: float=Field(default=.10, ge=0, le=.5)
    haircut: float=Field(default=.01, ge=0, le=.5)
    cost_per_stake: float=Field(default=0., ge=0, le=.2)
    min_edge: float=Field(default=.02, ge=0, le=2)
    stake_mode: Literal['adaptive','fixed']='adaptive'
    fixed_stake: float=1.
    max_stake: float=20.
    max_fraction: float=Field(default=.05, gt=0, le=1)
    drawdown_limit: float=Field(default=4., gt=0, le=100000)
    synthetic_margin: float=Field(default=.10, ge=0, le=.5)
    max_multiplier: float=Field(default=12., ge=1.1, le=100)
    volatility_floor: float=Field(default=.06, ge=.001, le=10)

    @field_validator('multipliers')
    @classmethod
    def valid_m(cls,v):
        if any(not math.isfinite(x) or x<1.01 or x>100 for x in v): raise ValueError('Multiplicateurs entre 1.01 et 100')
        return v
    @field_validator('fixed_stake','max_stake')
    @classmethod
    def valid_stake(cls,v):
        if v not in STAKES: raise ValueError('Mises possibles : 0.1 / 1 / 5 / 10 / 20')
        return v

# Deterministic quadrature, not a noisy 256-path Monte Carlo tail.
_GX, _GW = np.polynomial.hermite.hermgauss(48)
_GW = _GW / math.sqrt(math.pi)

def quotes06(history, price, start, asof, base_row, rules):
    if not math.isfinite(price) or price<=0 or not asof<start: raise ValueError('Cotation hors delai')
    if len(history)<11: raise ValueError('Historique insuffisant pour coter')
    if rules.quote_mode=='manual':
        displayed=list(rules.multipliers)
        gross=[m+(1 if rules.convention=='profit' else 0) for m in displayed]
        probs=None; sigma=None;source='manual_hypothesis_NOT_EUPHORIA'
    else:
        diffs=np.diff(np.array(history,dtype=float))
        if not np.isfinite(diffs).all(): raise ValueError('Historique invalide')
        # Explicit stress assumption: a floor avoids infinitesimal short-sample vol.
        # max of 10s/30s RMS is causal; it is NOT a fitted Euphoria issuer.
        sigma=max(rules.volatility_floor, float(np.sqrt(np.mean(diffs**2))),
                  float(np.sqrt(np.mean(diffs[-10:]**2))))
        lead=max(0.,start-asof)
        xs=price+sigma*math.sqrt(2*lead)*_GX
        probs=[]
        for shift in (1,0,-1):
            lo=(base_row+shift)*.5;hi=lo+.5
            distance=np.where(xs<lo,lo-xs,np.where(xs>hi,xs-hi,0.))
            hit=np.array([math.erfc(float(d)/(sigma*math.sqrt(10))) for d in distance])
            probs.append(float(np.clip(_GW@hit,1e-8,1)))
        # No floor above 1: never fabricate a positive-paying certain event.
        gross=[min(rules.max_multiplier,(1-rules.synthetic_margin)/p)
               if (1-rules.synthetic_margin)/p>=1.01 else None for p in probs]
        displayed=gross.copy();source='synthetic_continuous_touch_v2_NOT_EUPHORIA'
    return {'displayed':displayed,'gross':gross,
            'effective_gross':[m*(1-rules.haircut) if m is not None else None for m in gross],
            'source':source,'synthetic':True,'convention':rules.convention if rules.quote_mode=='manual' else 'total',
            'asof':float(asof),'base_row':int(base_row),'start':float(start),'end':float(start+5),
            'reference_at_quote':float(price),'issuer_probabilities':probs,'sigma_model':sigma,
            'haircut':rules.haircut,'cost_per_stake':rules.cost_per_stake,
            'valid':[m is not None for m in gross]}

def net_result(hit, gross, stake, cost):
    if gross is None or not math.isfinite(gross) or gross<=0: raise ValueError('Multiplicateur non executable')
    # EUR fictifs. Rounding is only presentation, accounting retains full precision.
    return stake*((gross if hit else 0)-1-cost)

def growth(p, gross, stake, capital, cost):
    loss=stake*(1+cost)
    if capital<=loss: return None
    return p*math.log1p(stake*(gross-1-cost)/capital)+(1-p)*math.log1p(-loss/capital)

def choose06(probs, q, rules, capital, peak, reserved=0., enabled=False, feature_ready=True):
    """Discrete utility maximization under a precommitted risk budget.
    Not a learned or optimal real-world stake policy. No martingale.
    """
    candidates=[];offers=[]
    budget=min(capital*rules.max_fraction, capital-reserved)
    for a, p in enumerate(probs):
        m=q['effective_gross'][a]
        worst=m*(1-rules.max_slippage) if m is not None else None
        row={'p':p.get('p'),'lower':p.get('lower'),'n':p.get('n',0),'ready':bool(p.get('ready')),
             'break_even':(1+rules.cost_per_stake)/m if m else None,
             'ev':p['p']*m-1-rules.cost_per_stake if m and p.get('p') is not None else None,
             'ev_lower':p['lower']*worst-1-rules.cost_per_stake if m and p.get('lower') is not None else None,
             'proposed_stake':0.,'growth_lower':None,'worst_effective_gross':worst}
        if row['ready'] and row['ev_lower'] is not None and row['ev_lower']>rules.min_edge:
            sizes=[rules.fixed_stake] if rules.stake_mode=='fixed' else STAKES
            for s in sizes:
                if s>rules.max_stake or s*(1+rules.cost_per_stake)>budget+1e-9: continue
                g=growth(p['lower'],worst,s,capital,rules.cost_per_stake)
                if g is not None and g>0: offers.append((g,a,s))
            local=[v for v in offers if v[1]==a]
            if local: row['growth_lower'],_,row['proposed_stake']=max(local)
        candidates.append(row)
    reason=None
    if not enabled: reason='Politique en pause : collecte independante'
    elif reserved>0: reason='Une mise est deja reservee ou engagee'
    elif capital<=0: reason='Portefeuille fictif epuise'
    elif peak-capital>=rules.drawdown_limit: reason='Limite de perte depuis le pic atteinte'
    elif not feature_ready: reason='Liquidite absente ou perimee pour ce modele'
    elif not offers:
        reason='Calibration insuffisante ou hors domaine' if not any(x['ready'] for x in candidates) else 'Aucune mise compatible avec valeur et risque'
    if reason is None:
        g,a,s=max(offers);reason='Valeur prudente et taille sous plafond (hypothese)'
    else: g,a,s=None,None,0.
    return {'chosen':a,'action':('hausse','stable','baisse')[a] if a is not None else 'attendre',
            'stake':s,'reason':reason,'candidates':candidates,'growth_lower':g,'risk_budget':budget,
            'reserved_cost':s*(1+rules.cost_per_stake)}

def financial_signal(net, capital_before):
    # A bounded pedagogical signal relative to 5% of the pre-trade bankroll.
    v=math.tanh(net/max(.1,.05*capital_before))
    return {'net_eur':net,'relative_return':net/max(capital_before,1e-9),
            'dan_gain':max(v,0.),'dan_loss':max(-v,0.),'signed_utility':v,
            'scale_eur':max(.1,.05*capital_before),'weights_changed':False}
