"""Paper-policy lab. All opportunities (including waits) are settled and archived.
At most one funded paper position, up to four overlapping shadow windows.
No online weight changes: held-out calibrator remains paired to frozen weights.
"""
from __future__ import annotations
import copy
import json
import math
import sqlite3
import time
from pathlib import Path
import numpy as np
from .decision_brain import DecisionBrain, ENCODER
from .economics import EconomySettings, Calibrator, quote_vector, policy, payoff
from .live import GridObservation, row_of, first_window, MAX_TRIAL_TICKS
from .engine import stamp


def new_stats():
    return {'opportunities':0,'waits':0,'trades':0,'wins':0,'losses':0,'void':0,
            'net':0.,'staked':0.,'peak':0.,'max_drawdown':0.,'hits':[0,0,0],
            'always_net':[0.,0.,0.], 'valid_windows':0,'chosen':[0,0,0]}


class DecisionLab:
    def __init__(self,data:Path,market,book):
        self.market=market;self.book=book
        self.db=sqlite3.connect(data/'flytrade-decision05.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS deployments(id INTEGER PRIMARY KEY,payload TEXT);''')
        self.brain=DecisionBrain();self.calibrator=Calibrator();self.settings=EconomySettings()
        self.stats=new_stats();self.pending=[];self.counter=0;self.generation=1
        self.auto=False;self.next_auto=0.;self.last=None;self.last_decision=None
        self.logs=[];self.fatal=None;self.deployment=None
        raw=self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if raw:
            d=json.loads(raw[0]);self.restore(d)
            if self.pending:
                self.invalidate('Redemarrage : observation interrompue, aucune mise a jour')
        self.log('memoire','Alpha05 : memoire separee, mode manuel au demarrage.')

    def log(self,kind,message):
        self.logs.append({'time':stamp(),'kind':kind,'message':message});self.logs=self.logs[-60:]

    def checkpoint(self):
        return {'schema':1,'version':'0.5.0-alpha','brain':self.brain.to_dict(),
                'calibrator':self.calibrator.to_dict(),'settings':self.settings.model_dump(),
                'stats':self.stats,'pending':self.pending,'counter':self.counter,
                'generation':self.generation,'deployment':self.deployment,'last':self.last}

    def restore(self,d):
        self.brain=DecisionBrain.from_dict(d['brain']);self.calibrator=Calibrator.from_dict(d['calibrator'])
        self.settings=EconomySettings(**d['settings']);self.stats=d['stats'];self.pending=d['pending']
        self.counter=d['counter'];self.generation=d['generation'];self.deployment=d.get('deployment');self.last=d.get('last')
        if self.last:
            self.last=next((r for r in self.pending if r['id']==self.last['id']),self.last)

    def save(self,records=()):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(self.checkpoint(),allow_nan=False),))
            for r in records:self.db.execute('INSERT OR REPLACE INTO opportunities VALUES(?,?)',
                                             (r['id'],json.dumps(r,allow_nan=False)))
            self.db.commit()
        except Exception:
            self.db.rollback();self.auto=False;self.fatal='Ecriture impossible : simulation arretee. Verifier le disque.'
            raise

    def close(self):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.db.close()

    def configure(self,settings):
        if self.pending or self.auto:raise ValueError('Arreter auto et laisser finir les fenetres avant les reglages.')
        settings.check()
        if self.stats['opportunities'] and settings.initial_capital!=self.settings.initial_capital:
            raise ValueError('Capital initial verrouille pour cette session ; creer une nouvelle session.')
        before=self.settings;self.settings=settings
        try:self.save()
        except Exception:self.settings=before;raise

    @property
    def running(self):return bool(self.pending)

    def start(self,now=None):
        if self.fatal:raise ValueError(self.fatal)
        reason=self.market.ready_reason(now)
        if reason:raise ValueError(reason)
        if len(self.pending)>=5:raise ValueError('Trop de fenetres ouvertes')
        before=copy.deepcopy(self.checkpoint())
        t=self.market.estimated_time(now)
        if self.pending and t-self.pending[-1]['placed']<4.99:raise ValueError('Une nouvelle observation par bloc de 5 s maximum')
        history=self.market.history();ref=self.market.ticks[-1].price;row=row_of(ref)
        start=first_window(t);frac=(ref-row*.5)/.5
        obs=GridObservation(history,.5/ref*100,int(math.ceil(start+5-t)),frac,ref)
        book=self.book.snapshot(t,now)
        d=self.brain.predict(obs,book)
        probs=self.calibrator.predict(d.scores,self.brain.fingerprint())
        q=quote_vector(history,ref,start,t,row,self.settings)
        open_trade=any(p['policy']['chosen'] is not None for p in self.pending)
        p=policy(probs,q,self.settings,self.settings.initial_capital+self.stats['net'],
                 self.stats['peak']-self.stats['net'],open_trade,
                 not self.brain.use_liquidity or book['available'])
        # Refuse rather than silently change the quoted target during inference.
        placed=self.market.estimated_time(now)
        if start-placed<10:
            self.restore(before);raise ValueError('Limite de 10 s franchie pendant le calcul ; reessayer')
        self.counter+=1
        rec={'schema05':1,'id':self.counter,'generation':self.generation,
            'source':'coinbase-ETH-USD','status':'pending','epoch':self.market.epoch,
            'placed':placed,'start':start,'end':start+5,'deadline':start-10,
            'reference':ref,'base_row':row,'history':list(history),
            'history_end':math.floor(self.market.watermark),'book':book,
            'scores_before':d.scores.tolist(),'probabilities':probs,'quotes':q,
            'policy':p,'chosen':p['chosen'],'action':p['action'],
            'target_row':row+(1,0,-1)[p['chosen']] if p['chosen'] is not None else None,
            'window_ticks':[],'touches':[False]*3,'observed':0,'hit':False,
            'settings':self.settings.model_dump(),'brain_fingerprint':self.brain.fingerprint(),
            'encoder':ENCODER,'prediction_cutoff':t,'net':None,'created_at':stamp()}
        self.pending.append(rec);self.stats['opportunities']+=1
        self.stats['waits']+=int(p['chosen'] is None)
        self.last=rec;self.last_decision=d
        try:self.save()
        except Exception:self.restore(before);raise
        self.log('decision',f"#{rec['id']} {p['action'].upper()} : {p['reason']}")
        self.next_auto=(math.floor(placed/5)+1)*5
        return rec

    def observe(self,tick):
        for r in self.pending:
            if r['epoch']!=tick.epoch:continue
            if r['start']<=tick.t<r['end']:
                if len(r['window_ticks'])>=MAX_TRIAL_TICKS:
                    self.invalidate('Trop de transactions dans une fenetre');return
                r['window_ticks'].append([tick.t,tick.price,tick.trade_id]);r['observed']+=1
                for a,shift in enumerate((1,0,-1)):
                    if row_of(tick.price)==r['base_row']+shift:r['touches'][a]=True
                if r['chosen'] is not None:r['hit']=r['touches'][r['chosen']]

    def finish_ready(self):
        completed=[r for r in self.pending if self.market.watermark>=r['end']+.5]
        if not completed:return
        before=copy.deepcopy(self.checkpoint())
        for r in completed:
            if not r['observed']:
                r.update(status='void',reason='Aucune transaction observee',net=0.)
                self.stats['void']+=1
            else:
                self.stats['valid_windows']+=1
                s=EconomySettings(**r['settings']);a=r['chosen']
                for i in range(3):
                    self.stats['hits'][i]+=int(r['touches'][i])
                    self.stats['always_net'][i]+=payoff(r['touches'][i],r['quotes']['effective_gross'][i],s.stake,s.cost_per_stake)
                if a is None:r.update(status='observed',net=0.)
                else:
                    net=payoff(r['touches'][a],r['quotes']['effective_gross'][a],s.stake,s.cost_per_stake)
                    r.update(status='won' if r['touches'][a] else 'lost',net=net)
                    self.stats['trades']+=1;self.stats['wins' if r['touches'][a] else 'losses']+=1
                    self.stats['staked']+=s.stake;self.stats['net']+=net;self.stats['chosen'][a]+=1
                    self.stats['peak']=max(self.stats['peak'],self.stats['net'])
                    self.stats['max_drawdown']=max(self.stats['max_drawdown'],self.stats['peak']-self.stats['net'])
            r['finished_at']=stamp()
            self.log('resultat',f"#{r['id']} {r['status']} : {r['net']:+.2f} USD fictifs ; touches {r['touches']}")
        self.pending=[r for r in self.pending if r not in completed]
        try:self.save(completed)
        except Exception:self.restore(before);raise

    def invalidate(self,reason):
        self.auto=False
        if not self.pending:return
        before=copy.deepcopy(self.checkpoint());records=self.pending
        for r in records:r.update(status='void',reason=reason,net=0.,finished_at=stamp())
        self.stats['void']+=len(records);self.pending=[]
        try:self.save(records)
        except Exception:self.restore(before);raise
        self.log('securite',reason+' ; aucun resultat financier ni apprentissage.')

    def tick(self,now=None):
        if self.fatal:return
        if not self.market.transport_healthy(now):
            if self.pending or self.auto:self.invalidate('Flux prix interrompu')
            return
        if any(r['epoch']!=self.market.epoch for r in self.pending):self.invalidate('Discontinuite du flux');return
        self.finish_ready()
        if self.auto and self.market.estimated_time(now)>=self.next_auto and not self.market.ready_reason(now):
            try:self.start(now)
            except ValueError as e:
                self.next_auto=self.market.estimated_time(now)+5;self.log('attente',str(e))

    def set_auto(self,on):
        if on and (self.fatal or self.market.ready_reason()):raise ValueError(self.fatal or self.market.ready_reason())
        self.auto=on;self.log('controle','Collecte automatique active' if on else 'Auto arrete ; fenetres ouvertes terminees normalement')

    def deploy(self,brain,calibrator,metadata):
        if self.pending or self.auto:raise ValueError('Arreter auto et laisser finir les observations live')
        before=copy.deepcopy(self.checkpoint())
        self.db.execute('INSERT INTO deployments VALUES (?,?)',(time.time_ns(),json.dumps(before)));self.db.commit()
        self.brain=DecisionBrain.from_dict(brain.to_dict());self.calibrator=Calibrator.from_dict(calibrator.to_dict())
        self.stats=new_stats();self.generation+=1;self.last=None;self.last_decision=None
        self.deployment=metadata;self.settings.decision_mode='collect'
        try:self.save()
        except Exception:self.restore(before);raise
        self.log('transfert','Poids et calibration copies. Poids GELES. Selectionner Politique pour autoriser les mises fictives.')

    def export_rows(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM opportunities ORDER BY id')]

    def snapshot(self,now=None):
        market=self.market.snapshot(now);t=market['exchange_time'];ref=market['price']
        preview=None
        if ref is not None and t:
            row=row_of(ref);start=first_window(t);h=self.market.history()
            preview={'base_row':row,'start':start,'end':start+5}
            if h:preview['quotes']=quote_vector(h,ref,start,t,row,self.settings)
        stats=copy.deepcopy(self.stats)
        stats.update(capital=self.settings.initial_capital+stats['net'],updates=self.brain.updates,
                     coverage=stats['trades']/stats['valid_windows'] if stats['valid_windows'] else 0.,
                     hit_rate=stats['wins']/stats['trades'] if stats['trades'] else None)
        recent=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM opportunities ORDER BY id DESC LIMIT 15')]
        fields=('id','status','action','net','touches','policy','quotes','start','end','placed')
        return {'version':'0.5.0-alpha','model':'decision05','market':market,
            'book':self.book.snapshot(t,now),'stats':stats,'settings':self.settings.model_dump(),
            'auto':self.auto,'pending':len(self.pending),'preview':preview,
            'active_trade':next(({k:v for k,v in r.items() if k!='window_ticks'} for r in self.pending if r['chosen'] is not None),None),'last':{k:v for k,v in self.last.items() if k!='window_ticks'} if self.last else None,
            'neural':self.brain.neural(self.last_decision),'logs':self.logs,'error':self.fatal,
            'calibration':{'n':self.calibrator.n,'min_total':self.calibrator.min_total,'min_bin':self.calibrator.min_bin,
                           'model_matches':self.calibrator.fingerprint==self.brain.fingerprint()},
            'deployment':self.deployment,'recent':[{k:r.get(k) for k in fields} for r in recent]}
