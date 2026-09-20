"""Chronological alpha05 replay: TRAIN -> calibration -> separate frozen TEST.
Outcome balancing only on TRAIN; calibration keeps natural multi-touch outcomes.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from .dataset import normalize,split_chronological,curriculum,canonical,MAX_BYTES,MAX_EPISODES
from .decision_brain import DecisionBrain
from .economics import EconomySettings,Calibrator,quote_vector,policy,payoff
from .compartment_brain import CircuitSettings
from .engine import stamp


class AcademyConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    seed:int=Field(default=42,ge=0,le=2147483647)
    epochs:int=Field(default=5,ge=1,le=20)
    mode:Literal['curriculum','balanced','chronological']='curriculum'
    signal:Literal['all','chosen']='all'
    learning_rate:float=Field(default=.25,gt=0,le=1)
    epsilon:float=Field(default=.15,ge=0,le=1)
    sparsity:float=Field(default=.05,ge=.01,le=.5)
    use_liquidity:StrictBool=False
    delay:float=Field(default=.3,ge=.05,le=3)


def parse05(raw):
    if not raw or len(raw)>MAX_BYTES:raise ValueError('Fichier vide ou superieur a 25 Mio')
    lines=raw.decode('utf-8-sig').splitlines()
    if len(lines)>MAX_EPISODES:raise ValueError('5000 observations maximum par import')
    accepted={};rejected=Counter()
    for line in lines:
        if not line.strip():continue
        try:
            row=json.loads(line,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('non_finite')))
            if not isinstance(row,dict):raise ValueError('objet_attendu')
            normalized=copy.deepcopy(row)
            if row.get('schema05')==1 and row.get('status') in ('observed','won','lost'):
                # A virtual probe validates geometry ONLY; never counted as a funded trade.
                normalized['chosen']=0
                normalized['status']='won' if row['touches'][0] else 'lost'
            ep=normalize(normalized)
            book=row.get('book')
            if book is not None and not isinstance(book,dict):raise ValueError('liquidite_invalide')
            if book is not None and type(book.get('available')) is not bool:raise ValueError('liquidite_invalide')
            if book and book.get('available'):
                if not math.isfinite(float(book['captured_at'])) or book['captured_at']>ep.placed+1e-6 or ep.placed-book['captured_at']>3:
                    raise ValueError('liquidite_non_causale')
                for k in ('spread','bid_depth','ask_depth','buy_volume_5s','sell_volume_5s'):
                    if not math.isfinite(float(book[k])) or book[k]<0:raise ValueError('liquidite_invalide')
                for k in ('imbalance','flow_imbalance'):
                    if not math.isfinite(float(book[k])) or not -1<=book[k]<=1:raise ValueError('liquidite_invalide')
            value={'episode':ep.to_dict(),'book':book if book and book.get('available') else None}
            if ep.uid in accepted:
                if accepted[ep.uid]!=value:raise ValueError('doublon_contradictoire')
                rejected['doublon']+=1;continue
            accepted[ep.uid]=value
        except (ValueError,TypeError,KeyError,IndexError,OverflowError) as e:
            msg=str(e) if isinstance(e,ValueError) and len(str(e))<80 else 'schema_invalide'
            rejected[msg]+=1
    if not accepted:raise ValueError('Aucune fenetre exploitable : '+str(dict(rejected)))
    return accepted,{'sha256':hashlib.sha256(raw).hexdigest(),'lines':len(lines),'accepted':len(accepted),
                     'with_liquidity':sum(v['book'] is not None for v in accepted.values()),'rejected':dict(rejected)}


class Academy:
    def __init__(self,data:Path):
        self.db=sqlite3.connect(data/'flytrade-academy05.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS archives(id INTEGER PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS corpus(id INTEGER PRIMARY KEY,payload TEXT);''')
        self.data={};self._data_dirty=False;self.import_report=None;self.config=AcademyConfig();self.economy=EconomySettings()
        self.brain=DecisionBrain();self.calibrator=Calibrator();self.plan=[];self.position=0
        self.splits=None;self.split_report=None;self.session=None;self.records=[];self.calibration_rows=[]
        self.last=None;self.pending=None;self.auto=False;self.fatal=None;self.test_opened=False;self.logs=[]
        raw=self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if raw:
            d=json.loads(raw[0])
            if 'data' not in d:
                corpus=self.db.execute('SELECT payload FROM corpus WHERE id=1').fetchone()
                d['data']=json.loads(corpus[0]) if corpus else {}
            self.restore(d)

    def checkpoint(self):
        return {'data':self.data,'import_report':self.import_report,'config':self.config.model_dump(),
                'economy':self.economy.model_dump(),'brain':self.brain.to_dict(),
                'calibrator':self.calibrator.to_dict(),'plan':self.plan,'position':self.position,
                'splits':self.splits,'split_report':self.split_report,'session':self.session,
                'records':self.records,'calibration_rows':self.calibration_rows,'last':self.last,
                'test_opened':self.test_opened}

    def restore(self,d):
        self.config=AcademyConfig(**d['config']);self.economy=EconomySettings(**d['economy'])
        self.brain=DecisionBrain.from_dict(d['brain']);self.calibrator=Calibrator.from_dict(d['calibrator'])
        for k in ('data','import_report','plan','position','splits','split_report','session','records','calibration_rows','last','test_opened'):
            setattr(self,k,d[k])
        self.pending=None;self.auto=False

    def save(self):
        try:
            d=self.checkpoint();d.pop('data')
            with self.db:
                if self._data_dirty:self.db.execute('INSERT OR REPLACE INTO corpus VALUES(1,?)',(json.dumps(self.data,allow_nan=False),))
                self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(d,allow_nan=False),))
            self._data_dirty=False
        except Exception:
            self.auto=False;self.fatal='Sauvegarde du protocole impossible';raise

    def idle(self):
        if self.auto or self.pending:raise ValueError('Mettre en pause et attendre la fin de ce pas')
        if self.fatal:raise ValueError(self.fatal)

    def archive(self):
        if self.session:
            with self.db:self.db.execute('INSERT INTO archives VALUES(?,?)',(time.time_ns(),json.dumps(self.report(),allow_nan=False)))

    def import_data(self,raw):
        self.idle();new,rep=parse05(raw)
        self.archive();old=copy.deepcopy(self.checkpoint())
        self.data=new;self._data_dirty=True;self.import_report=rep;self.plan=[];self.position=0;self.session=None
        self.records=[];self.splits=None;self.split_report=None;self.last=None;self.test_opened=False
        try:self.save()
        except Exception:self.restore(old);raise

    def begin(self,config,economy):
        from .dataset import Episode
        self.idle()
        if not self.data:raise ValueError('Importer des observations avant de commencer')
        data=[Episode.from_dict(v['episode']) for v in self.data.values() if not config.use_liquidity or v['book'] is not None]
        if config.use_liquidity and len(data)<30:raise ValueError('Collecter au moins 30 fenetres avec carnet reel ; les anciens exports ne le contiennent pas')
        splits,report=split_chronological(data)
        if config.mode=='chronological':train=[('C / naturel',e) for e in splits['train']]
        else:
            train=[(phase,e) for phase,e in curriculum(splits,config.epochs,config.seed) if not phase.startswith('validation')]
            if config.mode=='balanced':train=[(p,e) for p,e in train if p.startswith('A')]
        plan=[[p,e.uid] for p,e in train]+[['calibration',e.uid] for e in splits['validation']]
        self.archive();old=copy.deepcopy(self.checkpoint())
        self.config=config;self.economy=economy.model_copy(deep=True);self.economy.decision_mode='policy'
        self.brain=DecisionBrain(config.seed,config.sparsity,config.use_liquidity)
        self.calibrator=Calibrator();self.session=str(time.time_ns());self.plan=plan;self.position=0
        self.splits={k:[e.uid for e in v] for k,v in splits.items()};self.split_report=report
        self.records=[];self.last=None;self.test_opened=False;self.calibration_rows=[]
        try:self.save()
        except Exception:self.restore(old);raise

    def prepare(self):
        from .dataset import Episode
        if self.fatal:raise ValueError(self.fatal)
        if self.pending:raise ValueError('Un pas est en cours')
        if self.position>=len(self.plan):raise ValueError('Protocole termine')
        phase,uid=self.plan[self.position];v=self.data[uid];ep=Episode.from_dict(v['episode'])
        old=copy.deepcopy(self.brain.to_dict())
        d=self.brain.predict(ep.observation(),v['book'],self.config.epsilon if phase not in ('calibration','test') else 0.)
        q=quote_vector(ep.history,ep.reference,ep.start,ep.placed,ep.base_row,self.economy)
        probs=self.calibrator.predict(d.scores,self.brain.fingerprint())
        previous=[r for r in self.records if r['phase']=='test' and r['policy']['chosen'] is not None]
        matured=[r for r in previous if r['end']<=ep.placed]
        pnl=np.cumsum([0.]+[r['net_hypothetical'] for r in matured])
        open_position=any(r['end']>ep.placed for r in previous)
        pol=policy(probs,q,self.economy,self.economy.initial_capital+float(pnl[-1]),
                   float(pnl.max()-pnl[-1]),open_position,not self.config.use_liquidity or bool(v['book']))
        self.pending={'decision':d,'episode':ep,'phase':phase,'ready':time.monotonic()+self.config.delay,'old_brain':old,'quotes':q,'probabilities':probs,'policy':pol}
        self.last={'phase':phase,'uid':uid,'reference':ep.reference,'base_row':ep.base_row,
                   'start':ep.start,'end':ep.end,'placed':ep.placed,'history':ep.history,
                   'history_end':ep.history_end,'chosen':d.action,'scores':d.scores.tolist(),
                   'quotes':q,'policy':pol,'status':'pending','neural':self.brain.neural(d)}

    def finish(self):
        p=self.pending
        if not p:return
        old=copy.deepcopy(self.checkpoint());old['brain']=p['old_brain'];old['last']=None
        d,ep,phase=p['decision'],p['episode'],p['phase'];learning=phase not in ('calibration','test')
        result=self.brain.learn_outcomes(d,list(ep.touches),self.config.learning_rate,self.config.signal) if learning else None
        if phase=='calibration':self.calibration_rows.append([d.scores.tolist(),list(ep.touches)])
        chosen=p['policy']['chosen'] if phase=='test' else None
        gross=p['quotes']['effective_gross'];s=self.economy
        net=payoff(ep.touches[chosen],gross[chosen],s.stake,s.cost_per_stake) if chosen is not None else 0.
        r={'phase':phase,'uid':ep.uid,'placed':ep.placed,'start':ep.start,'end':ep.end,'chosen_forecast':d.action,'won_forecast':bool(ep.touches[d.action]),
           'touches':list(ep.touches),'scores_before':d.scores.tolist(),'probabilities':p['probabilities'],
           'policy':p['policy'],'quotes':p['quotes'],'net_hypothetical':net,
           'always_net':[payoff(ep.touches[i],gross[i],s.stake,s.cost_per_stake) for i in range(3)],
           'updated':learning,'at':stamp()}
        self.records.append(r);self.position+=1
        self.last.update(status='won' if ep.touches[d.action] else 'lost',hit=bool(ep.touches[d.action]),
                         touches=list(ep.touches),ticks=list(ep.ticks),neural=self.brain.neural(d,result))
        if self.position==len(self.plan):
            self.auto=False
            if phase=='calibration':
                self.calibrator.fit([r[0] for r in self.calibration_rows],[r[1] for r in self.calibration_rows],self.brain.fingerprint())
        self.pending=None
        try:self.save()
        except Exception:self.restore(old);raise

    def tick(self):
        if self.fatal:return
        if self.pending and time.monotonic()>=self.pending['ready']:self.finish()
        elif self.auto and not self.pending and self.position<len(self.plan):self.prepare()

    def open_test(self):
        self.idle()
        if not self.session or self.position!=len(self.plan):raise ValueError('Terminer entrainement et calibration')
        if self.test_opened:raise ValueError('Test deja consulte pour cette session. Collecter de nouvelles donnees avant de conclure.')
        old=copy.deepcopy(self.checkpoint())
        self.plan.extend([['test',uid] for uid in self.splits['test']]);self.test_opened=True
        try:self.save()
        except Exception:self.restore(old);raise

    def metrics(self):
        out={}
        for phase in dict.fromkeys(r['phase'] for r in self.records):
            rows=[r for r in self.records if r['phase']==phase];n=len(rows)
            chosen=[r for r in rows if r['policy']['chosen'] is not None] if phase=='test' else []
            out[phase]={'n':n,'wins':sum(r['won_forecast'] for r in rows),'choices':[sum(r['chosen_forecast']==i for r in rows) for i in range(3)],
               'always_hits':[sum(r['touches'][i] for r in rows) for i in range(3)],
               'paper_trades':len(chosen),'paper_net':sum(r['net_hypothetical'] for r in chosen),
               'always_net':[sum(r['always_net'][i] for r in rows) for i in range(3)] if phase=='test' else None}
            if phase=='test':
                b=[]
                for a in range(3):
                    valid=[r for r in rows if r['probabilities'][a]['p'] is not None]
                    b.append(sum((r['probabilities'][a]['p']-r['touches'][a])**2 for r in valid)/len(valid) if valid else None)
                out[phase]['brier']=b
        return out

    def report(self):
        # Tests' targets/scores are never exported before explicit open_test.
        split=copy.deepcopy(self.split_report)
        if split and not self.test_opened:split['splits']['test']={'n':split['splits']['test']['n'],'hidden':True}
        return {'version':'0.5.0-alpha','session':self.session,'import':self.import_report,
            'split':split,'config':self.config.model_dump(),'economy_hypotheses':self.economy.model_dump(),
            'position':self.position,'total':len(self.plan),'metrics':self.metrics(),'records':self.records,
            'calibration':self.calibrator.to_dict(),'brain_fingerprint':self.brain.fingerprint(),
            'warning':'Calibration est un ajustement, pas un test. PnL hypothetique, pas des cotations Euphoria historiques.'}

    def snapshot(self):
        split=copy.deepcopy(self.split_report)
        if split and not self.test_opened:split['splits']['test']={'n':split['splits']['test']['n'],'hidden':True}
        return {'import':self.import_report,'config':self.config.model_dump(),'split':split,'session':self.session,
            'position':self.position,'total':len(self.plan),'auto':self.auto,'running':self.pending is not None,
            'done':bool(self.session) and self.position==len(self.plan),'test_opened':self.test_opened,
            'last':self.last,'metrics':self.metrics(),'calibration_n':self.calibrator.n,
            'calibration_sufficient':self.calibrator.n>=self.calibrator.min_total,
            'updates':self.brain.updates,'error':self.fatal}

    def close(self):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.db.close()
