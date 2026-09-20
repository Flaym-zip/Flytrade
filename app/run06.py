"""Durable collector and paper execution simulator, with independent controls.
Preview follows price. Submitted contract geometry never follows price.
"""
from __future__ import annotations
import copy
import json
import math
import shutil
import sqlite3
import time
from pathlib import Path
from .brain06 import Brain06, ENCODER06
from .economy06 import Rules06, Calibrator, quotes06, choose06, net_result, financial_signal
from .live import GridObservation, row_of, first_window, MAX_TRIAL_TICKS
from .engine import stamp


def stats06():
    return dict(opportunities=0, waits=0, trades=0, wins=0, losses=0, void=0, rejects=0,
                valid_windows=0, net=0., staked=0., peak_capital=20., max_drawdown=0.,
                hits=[0,0,0], chosen=[0,0,0], sessions_initial=20.)


class Run06:
    def __init__(self, data: Path, market, book):
        self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True)
        self.market=market;self.book=book
        self.db=sqlite3.connect(self.data/'flytrade06.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY,generation INTEGER,placed REAL,status TEXT,payload TEXT);
        CREATE INDEX IF NOT EXISTS opportunities_time ON opportunities(placed);
        CREATE TABLE IF NOT EXISTS archives(id INTEGER PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS ticks(trade_id INTEGER PRIMARY KEY,t REAL,price REAL,epoch INTEGER,received REAL);
        CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY AUTOINCREMENT,t REAL,payload TEXT);
        CREATE TABLE IF NOT EXISTS gaps(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT,reason TEXT);''')
        self.brain=Brain06();self.calibrator=Calibrator(min_total=60,min_bin=20)
        self.rules=Rules06();self.stats=stats06();self.counter=0;self.generation=1
        self.pending=[];self.collect=False;self.policy_enabled=False;self.next_collect=0.
        self.last=None;self.last_decision=None;self.last_financial=None;self.deployment=None
        self.logs=[];self.fatal=None;self._dataset_cache=None;self._dataset_cache_at=0.;self._raw=[];self._samples=[];self._last_sample=0.;self._last_flush=0.
        raw=self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if raw:
            self.restore(json.loads(raw[0]));self.policy_enabled=False
            if self.pending:self.invalidate('Redemarrage : contrats interrompus, resultat inconnu',keep_collect=True)
        self.log('etat','Alpha07 : portefeuille EUR fictif ; source '+getattr(self.market,'source_id','coinbase-ETH-USD'))
        self.save()

    def log(self,kind,msg):
        self.logs.append({'time':stamp(),'kind':kind,'message':msg});self.logs=self.logs[-80:]

    def checkpoint(self):
        return dict(schema=1,version='0.7.0-alpha',brain=self.brain.to_dict(),calibrator=self.calibrator.to_dict(),
            settings=self.rules.model_dump(),stats=self.stats,counter=self.counter,generation=self.generation,
            pending=self.pending,collect=self.collect,last=self.last,last_financial=self.last_financial,
            deployment=self.deployment,currency='EUR_PAPER_ONLY',price_currency='USD')

    def restore(self,d):
        schema=d['brain'].get('schema')
        key='live:'+str(self.data.absolute())+':'+str(d.get('generation',1))
        self.brain=Brain06.from_dict(d['brain']) if schema==Brain06.SCHEMA else Brain06.import_legacy(d['brain'],key)
        self.calibrator=Calibrator.from_dict(d['calibrator'])
        if self.calibrator.n and self.calibrator.fingerprint!=self.brain.fingerprint():raise ValueError('Calibration incompatible avec les poids live')
        self.rules=Rules06(**d['settings']);self.stats=d['stats'];self.pending=d['pending']
        self.counter=d['counter'];self.generation=d['generation'];self.collect=d.get('collect',False)
        self.last=d.get('last');self.last_financial=d.get('last_financial');self.deployment=d.get('deployment')
        if self.last:self.last=next((p for p in self.pending if p['id']==self.last['id']),self.last)
        if getattr(self,'fatal',None):self.collect=False;self.policy_enabled=False

    def save(self, records=()):
        try:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps(self.checkpoint(),allow_nan=False),))
                for r in records:
                    self.db.execute('INSERT OR REPLACE INTO opportunities VALUES (?,?,?,?,?)',
                        (r['id'],r['generation'],r['placed'],r['status'],json.dumps(r,allow_nan=False)))
        except Exception:
            self.policy_enabled=False;self.collect=False;self.fatal='Ecriture impossible : collecte et politique arretees'
            raise

    @property
    def capital(self):return 20.+self.stats['net']
    @property
    def reserved(self):
        return sum(r['policy']['reserved_cost'] for r in self.pending if r['order_status'] in ('submitted','locked'))

    def configure(self,rules):
        if self.pending or self.policy_enabled:raise ValueError('Mettre collecte et politique en pause, laisser finir les fenetres')
        if self.collect:raise ValueError('Mettre la collecte en pause avant les reglages')
        old=self.rules;self.rules=rules
        try:self.save()
        except Exception:self.rules=old;raise

    def controls(self,collect=None,policy=None):
        if self.fatal:raise ValueError(self.fatal)
        if collect is False:self.policy_enabled=False
        if collect is not None:
            if collect and shutil.disk_usage(self.data).free<200*1024*1024:raise ValueError('Moins de 200 Mio libres')
            self.collect=collect
        if policy is not None:self.policy_enabled=policy
        # policy can remain armed while collector warms up, never executes sans feed.
        if self.policy_enabled and not self.collect:
            self.collect=True
        self.save()
        self.log('controle',f'Collecte {self.collect} ; politique {self.policy_enabled}')

    def start(self,now=None):
        if self.fatal:raise ValueError(self.fatal)
        reason=self.market.ready_reason(now)
        if reason:raise ValueError(reason)
        if len(self.pending)>=8:raise ValueError('File de fenetres pleine')
        t=self.market.estimated_time(now)
        if self.pending and math.floor(t/5)==math.floor(self.pending[-1]['placed']/5):raise ValueError('Une fenetre par bloc maximum')
        before=copy.deepcopy(self.checkpoint())
        history=self.market.history();ref=self.market.ticks[-1].price;row=row_of(ref)
        start=first_window(t+self.rules.execution_delay)
        obs=GridObservation(history,.5/ref*100,int(math.ceil(start+5-t)),(ref-row*.5)/.5,ref)
        book=self.book.snapshot(t,now)
        d=self.brain.predict(obs,book)
        q=quotes06(history,ref,start,t,row,self.rules)
        probs=self.calibrator.predict(d.scores,self.brain.fingerprint())
        pol=choose06(probs,q,self.rules,self.capital,self.stats['peak_capital'],self.reserved,
                     self.policy_enabled,not self.brain.use_liquidity or book['available'])
        placed=self.market.estimated_time(now)
        if start-placed-self.rules.execution_delay<10-1e-6:
            self.restore(before);raise ValueError('Duree de calcul trop longue : nouvelle cotation necessaire')
        self.counter+=1
        chosen=pol['chosen']
        neural_action=('hausse','stable','baisse')[d.action]
        economic_candidate=pol['candidates'][chosen] if chosen is not None else None
        rec=dict(schema06=1,id=self.counter,generation=self.generation,source=getattr(self.market,'source_id','coinbase-ETH-USD'),
            status='pending',epoch=self.market.epoch,placed=placed,start=start,end=start+5,deadline=start-10,
            reference=ref,base_row=row,history=list(history),history_end=math.floor(self.market.watermark),book=book,
            scores_before=d.scores.tolist(),probabilities=probs,quote_click=q,quotes=q,quote_lock=None,
            lock_due=placed+self.rules.execution_delay,locked_at=None,order_status='submitted' if chosen is not None else 'shadow',
            policy=pol,chosen=chosen,action=pol['action'],stake=pol['stake'],
            brain_scores=d.scores.tolist(),brain_preferred_action=neural_action,
            brain_tie_break={'used':bool(d.extra.get('tie_break')),'candidates':[('hausse','stable','baisse')[i] for i in d.extra.get('preferred_candidates',[d.action])],'selected':neural_action},
            economic_action=pol['action'],economic_reason=pol['reason'],
            multiplier=q['effective_gross'][chosen] if chosen is not None else None,
            EV=economic_candidate['ev'] if economic_candidate else None,
            risk_decision={'approved':chosen is not None,'risk_budget':pol['risk_budget'],'growth_lower':pol['growth_lower'],'ev_lower':economic_candidate['ev_lower'] if economic_candidate else None},
            economic_stake=pol['stake'],
            target_row=row+(1,0,-1)[chosen] if chosen is not None else None,
            window_ticks=[],touches=[False]*3,observed=0,hit=False,net=None,
            settings=self.rules.model_dump(),brain_fingerprint=self.brain.fingerprint(),encoder=ENCODER06,
            prediction_cutoff=t,created_at=stamp(),capital_before=self.capital,currency='EUR_PAPER_ONLY')
        self.pending.append(rec);self.stats['opportunities']+=1;self.stats['waits']+=int(chosen is None)
        self.last=rec;self.last_decision=d;self.next_collect=(math.floor(placed/5)+1)*5
        try:self.save()
        except Exception:self.restore(before);raise
        self.log('decision',f"#{rec['id']} {pol['action']} ; mise {pol['stake']:.2f} EUR ; {pol['reason']}")
        return rec

    def lock_ready(self,now=None):
        t=self.market.estimated_time(now)
        ready=[r for r in self.pending if r['quote_lock'] is None and not r.get('lock_attempted') and t>=r['lock_due']]
        if not ready:return
        before=copy.deepcopy(self.checkpoint())
        for r in ready:
            r['lock_attempted']=True;r['locked_at']=t
            reason=self.market.ready_reason(now)
            if r['epoch']!=self.market.epoch:reason='Changement de flux'
            if t>r['start']-10+1e-6:reason='Delai execution depasse (10 s de preavis non respectees)'
            if reason is None:
                rules=Rules06(**r['settings'])
                r['quote_lock']=quotes06(self.market.history(),self.market.ticks[-1].price,
                                         r['start'],t,r['base_row'],rules)
                r['lock_price']=self.market.ticks[-1].price
                a=r['chosen']
                if a is not None:
                    got=r['quote_lock']['gross'][a];promised=r['quote_click']['gross'][a]
                    if got is None:reason='Aucune cotation executable'
                    elif got<promised*(1-rules.max_slippage)-1e-10:reason='Slippage au-dela de la tolerance fixee au clic'
            if r['chosen'] is not None:
                if reason:
                    r['order_status']='rejected';r['execution_reason']=reason;self.stats['rejects']+=1
                    self.log('execution',f"#{r['id']} refuse : {reason} ; reservation liberee")
                else:
                    r['order_status']='locked'
                    self.log('execution',f"#{r['id']} multiplicateur bloque : x{r['quote_lock']['gross'][r['chosen']]:.3f}")
            elif reason:r['execution_reason']=reason
        try:self.save()
        except Exception:self.restore(before);raise

    def observe(self,tick):
        if self.collect:
            if len(self._raw)>=50000:
                self.fatal='Tampon ticks plein : collecte arretee, aucune troncature silencieuse';self.collect=False;self.policy_enabled=False
            else:self._raw.append((tick.trade_id,tick.t,tick.price,tick.epoch,time.time()))
        for r in self.pending:
            if r['epoch']==tick.epoch and r['start']<=tick.t<r['end']:
                if len(r['window_ticks'])>=MAX_TRIAL_TICKS:
                    self.invalidate('Trop de transactions par fenetre',keep_collect=False);return
                r['window_ticks'].append([tick.t,tick.price,tick.trade_id]);r['observed']+=1
                for a,shift in enumerate((1,0,-1)):
                    if row_of(tick.price)==r['base_row']+shift:r['touches'][a]=True
                if r['chosen'] is not None:r['hit']=r['touches'][r['chosen']]

    def finish_ready(self):
        ready=[r for r in self.pending if self.market.watermark>=r['end']+.5]
        if not ready:return
        before=copy.deepcopy(self.checkpoint())
        for r in ready:
            net=0.
            if not r['observed']:
                r.update(status='void',reason='Aucune transaction : resultat inconnu, remboursement fictif')
                self.stats['void']+=1
                if r['order_status']=='locked':r['order_status']='void'
            else:
                self.stats['valid_windows']+=1
                for a in range(3):self.stats['hits'][a]+=int(r['touches'][a])
                if r['order_status']=='locked':
                    a=r['chosen'];rules=Rules06(**r['settings'])
                    net=net_result(r['touches'][a],r['quote_lock']['effective_gross'][a],r['stake'],rules.cost_per_stake)
                    r['status']='won' if r['touches'][a] else 'lost';r['order_status']='settled'
                    self.stats['trades']+=1;self.stats['wins' if r['touches'][a] else 'losses']+=1
                    self.stats['staked']+=r['stake'];self.stats['chosen'][a]+=1
                    signal=financial_signal(net,r['capital_before']);r['financial_signal']=signal
                    self.last_financial=signal
                    self.stats['net']+=net;self.stats['peak_capital']=max(self.stats['peak_capital'],self.capital)
                    self.stats['max_drawdown']=max(self.stats['max_drawdown'],self.stats['peak_capital']-self.capital)
                else:
                    r['status']='observed'
                    if r['order_status']=='submitted':
                        r['order_status']='rejected';r['execution_reason']='Cotation non verrouillee';self.stats['rejects']+=1
            r['net']=net;r['finished_at']=stamp()
            self.log('resultat',f"#{r['id']} {r['status']} ; net {net:+.3f} EUR fictifs")
        self.pending=[r for r in self.pending if r not in ready]
        try:self.save(ready)
        except Exception:self.restore(before);raise

    def invalidate(self,reason,keep_collect=True):
        self.policy_enabled=False
        if not keep_collect:self.collect=False
        before=copy.deepcopy(self.checkpoint());ready=self.pending
        for r in ready:r.update(status='void',order_status='void',reason=reason,net=0.,finished_at=stamp())
        self.stats['void']+=len(ready);self.pending=[]
        try:
            self.save(ready)
            with self.db:self.db.execute('INSERT INTO gaps(at,reason) VALUES (?,?)',(stamp(),reason))
        except Exception:self.restore(before);raise
        self.log('flux',reason+' ; politique desarmee, reservation liberee, aucune perte supposee')

    def flush_raw(self):
        if not self._raw and not self._samples:return
        try:
            with self.db:
                self.db.executemany('INSERT OR IGNORE INTO ticks VALUES (?,?,?,?,?)',self._raw)
                self.db.executemany('INSERT INTO samples(t,payload) VALUES (?,?)',self._samples)
            self._raw.clear();self._samples.clear()
        except Exception:
            self.collect=False;self.policy_enabled=False;self.fatal='Archivage du flux impossible';raise

    def tick(self,now=None):
        mono=time.monotonic() if now is None else now
        if self.fatal:return
        if mono-self._last_flush>=1:
            self.flush_raw();self._last_flush=mono
        if not self.market.transport_healthy(now):
            if self.pending:self.invalidate('Flux prix interrompu',keep_collect=True)
            return
        if any(r['epoch']!=self.market.epoch for r in self.pending):
            self.invalidate('Discontinuite du flux',keep_collect=True);return
        t=self.market.estimated_time(now)
        if self.collect and t-self._last_sample>=1:
            if shutil.disk_usage(self.data).free<200*1024*1024:
                self.collect=False;self.policy_enabled=False;self.log('disque','Collecte stoppee : moins de 200 Mio libres');self.save();return
            sample=dict(price=self.market.ticks[-1].price if self.market.ticks else None,
                        epoch=self.market.epoch,book=self.book.snapshot(t,now),asof=t,received=time.time())
            self._samples.append((t,json.dumps(sample,allow_nan=False)));self._last_sample=t
        self.lock_ready(now);self.finish_ready()
        if self.collect and t>=self.next_collect and not self.market.ready_reason(now):
            try:self.start(now)
            except ValueError as e:self.next_collect=t+5;self.log('attente',str(e))

    def reset_wallet(self,reset_brain=False):
        if self.collect or self.policy_enabled or self.pending:raise ValueError('Mettre tout en pause et laisser finir les fenetres')
        old=copy.deepcopy(self.checkpoint())
        with self.db:self.db.execute('INSERT INTO archives VALUES (?,?)',(time.time_ns(),json.dumps(old,allow_nan=False)))
        self.stats=stats06();self.generation+=1;self.last=None;self.last_decision=None;self.last_financial=None
        if reset_brain:self.brain=Brain06();self.calibrator=Calibrator(min_total=60,min_bin=20);self.deployment=None
        try:self.save()
        except Exception:self.restore(old);raise
        self.log('reset','Session archivee. Portefeuille remis a 20 EUR fictifs. Corpus conserve.')

    def deploy(self,brain,calibrator,metadata):
        if self.collect or self.policy_enabled or self.pending:raise ValueError('Arreter collecte et politique, laisser finir les fenetres')
        newbrain=Brain06.from_dict(brain.to_dict());newcal=Calibrator.from_dict(calibrator.to_dict())
        if newcal.fingerprint!=newbrain.fingerprint():raise ValueError('Calibration et modele incompatibles')
        old=copy.deepcopy(self.checkpoint())
        self.brain=newbrain;self.calibrator=newcal;self.deployment=metadata
        self.stats=stats06();self.generation+=1;self.last=None;self.last_decision=None;self.last_financial=None
        try:
            with self.db:
                self.db.execute('INSERT INTO archives VALUES (?,?)',(time.time_ns(),json.dumps(old,allow_nan=False)))
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps(self.checkpoint(),allow_nan=False),))
        except Exception:self.restore(old);raise
        self.log('modele','Poids et calibration transferes, geles. Politique en pause.')

    def export_rows(self,limit=None):
        sql='SELECT payload FROM opportunities ORDER BY id'
        if limit:sql='SELECT payload FROM (SELECT id,payload FROM opportunities ORDER BY id DESC LIMIT ?) ORDER BY id'
        return [json.loads(x[0]) for x in self.db.execute(sql,(limit,) if limit else ())]

    def dataset_info(self):
        now=time.monotonic()
        if self._dataset_cache and now-self._dataset_cache_at<5:return dict(self._dataset_cache,collect=self.collect)
        n,lo,hi=self.db.execute('SELECT COUNT(*),MIN(placed),MAX(placed) FROM opportunities').fetchone()
        result=dict(windows=n,first=lo,last=hi,ticks=self.db.execute('SELECT COUNT(*) FROM ticks').fetchone()[0],
            snapshots=self.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0],
            gaps=self.db.execute('SELECT COUNT(*) FROM gaps').fetchone()[0],
            disk_bytes=sum(p.stat().st_size for p in self.data.glob('flytrade06.sqlite3*')),
            free_bytes=shutil.disk_usage(self.data).free,collect=self.collect)
        self._dataset_cache=result;self._dataset_cache_at=now
        return result

    def snapshot(self,now=None):
        m=self.market.snapshot(now);t=m['exchange_time'];ref=m['price'];preview=None
        if ref is not None and t and self.market.history():
            row=row_of(ref);start=first_window(t+self.rules.execution_delay)
            preview=dict(base_row=row,start=start,end=start+5,
                quotes=quotes06(self.market.history(),ref,start,t,row,self.rules))
        stat=copy.deepcopy(self.stats)
        stat.update(capital=self.capital,reserved=self.reserved,available=self.capital-self.reserved,
                    coverage=stat['trades']/stat['valid_windows'] if stat['valid_windows'] else 0,
                    hit_rate=stat['wins']/stat['trades'] if stat['trades'] else None,
                    updates=self.brain.updates,value_updates=self.brain.value_updates)
        def small(r):return {k:v for k,v in r.items() if k not in ('window_ticks','history','book')}
        last_rows=[json.loads(x[0]) for x in self.db.execute('SELECT payload FROM opportunities ORDER BY id DESC LIMIT 12')]
        return dict(version='0.7.0-alpha',market=m,book=self.book.snapshot(t,now),stats=stat,
            settings=self.rules.model_dump(),collect=self.collect,policy_enabled=self.policy_enabled,
            pending=len(self.pending),preview=preview,last=small(self.last) if self.last else None,
            active_trade=next((small(r) for r in self.pending if r['order_status'] in ('submitted','locked')),None),
            neural=self.brain.neural(self.last_decision),financial_signal=self.last_financial,
            logs=self.logs,error=self.fatal,deployment=self.deployment,recent=[small(r) for r in last_rows],
            calibration=dict(n=self.calibrator.n,min_total=self.calibrator.min_total,min_bin=self.calibrator.min_bin),
            dataset=self.dataset_info())

    def close(self):
        self.flush_raw();self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.db.close()
