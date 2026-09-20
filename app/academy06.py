"""Disk-backed corpus and isolated training worker. Shuffle TRAIN only.
No oracle labels enter decide(). Economic targets use recorded delayed quotes,
never newly invented historic quotes. The value head is auxiliary, not calibrated p.
"""
from __future__ import annotations
import copy, hashlib, json, math, sqlite3, time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from .dataset import normalize,Episode,split_chronological,canonical
from .brain06 import Brain06
from .compartment_brain import CircuitSettings
from .economics import Calibrator
from .economy06 import net_result
from .engine import stamp


class Config06(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    seed:int=Field(default=42,ge=0,le=2147483647)
    n_kc:Literal[1024,2048]=2048
    mode:Literal['curriculum','balanced','chronological']='curriculum'
    epochs:int=Field(default=5,ge=1,le=20)
    shuffle_train:StrictBool=False
    signal:Literal['all','chosen']='all'
    learning_rate:float=Field(default=.25,gt=0,le=1)
    epsilon:float=Field(default=.15,ge=0,le=1)
    sparsity:float=Field(default=.05,ge=.01,le=.5)
    use_liquidity:StrictBool=True
    economic_head:StrictBool=True
    max_windows:int=Field(default=10000,ge=30,le=50000)
    batch:int=Field(default=10,ge=1,le=40)


def validate_row(row):
    if not isinstance(row,dict):raise ValueError('objet_attendu')
    v=dict(row)
    if row.get('schema05')==1 or row.get('schema06')==1:
        if row.get('status') not in ('observed','won','lost'):raise ValueError('annule_ou_inacheve')
        v['chosen']=0;v['status']='won' if row['touches'][0] else 'lost'
    ep=normalize(v,20 if row.get('schema06')==1 else 15)
    book=row.get('book')
    if book and book.get('available'):
        captured=float(book['captured_at'])
        if not math.isfinite(captured) or captured>ep.placed+1e-6 or ep.placed-captured>3.:
            raise ValueError('carnet_non_causal')
        for k in ('spread','bid_depth','ask_depth','buy_volume_5s','sell_volume_5s'):
            if not math.isfinite(float(book[k])) or book[k]<0:raise ValueError('carnet_invalide')
        for k in ('imbalance','flow_imbalance'):
            if not math.isfinite(float(book[k])) or not -1<=book[k]<=1:raise ValueError('carnet_invalide')
    else:book=None
    quote=None
    if row.get('schema06')==1 and row.get('quote_lock'):
        q=row['quote_lock'];at=float(q['asof'])
        if not ep.placed<=at<=ep.start-10+1e-6:raise ValueError('execution_non_causale')
        if q.get('base_row')!=ep.base_row or q.get('start')!=ep.start:raise ValueError('contrat_execution_incoherent')
        m=q['effective_gross']
        if len(m)!=3 or any(x is not None and (not math.isfinite(x) or x<=0 or x>200) for x in m):
            raise ValueError('multiplicateur_invalide')
        cost=float(q.get('cost_per_stake',0))
        if not math.isfinite(cost) or not 0<=cost<=1:raise ValueError('cout_invalide')
        quote=q
    # Alpha05 quotes are explicitly not delayed fills; never treat them as such.
    return ep,dict(episode=ep.to_dict(),book=book,quote=quote)


class Academy06:
    def __init__(self,data,expected_source=None):
        self.expected_source=expected_source
        self.db=sqlite3.connect(Path(data)/'flytrade-training06.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS corpus(uid TEXT PRIMARY KEY,placed REAL,payload TEXT);
        CREATE INDEX IF NOT EXISTS corpus_placed ON corpus(placed);
        CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS records(session TEXT,step INTEGER,phase TEXT,payload TEXT,PRIMARY KEY(session,step));
        CREATE TABLE IF NOT EXISTS archives(session TEXT PRIMARY KEY,payload TEXT);''')
        self.config=Config06();self.brain=Brain06();self.calibrator=Calibrator(min_total=60,min_bin=20)
        self.session=None;self.plan=[];self.position=0;self.split_report=None;self.splits=None
        self.auto=False;self.error=None;self.test_opened=False;self.cal_scores=[];self.cal_labels=[]
        self.last=None;self.last_decision=None;self.metrics={};self.import_report=None
        raw=self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if raw:self.restore(json.loads(raw[0]))
        self.auto=False

    def checkpoint(self):
        return dict(version='0.7.0-alpha',config=self.config.model_dump(),brain=self.brain.to_dict(),
            calibrator=self.calibrator.to_dict(),session=self.session,plan=self.plan,position=self.position,
            split_report=self.split_report,splits=self.splits,test_opened=self.test_opened,
            cal_scores=self.cal_scores,cal_labels=self.cal_labels,last=self.last,metrics=self.metrics,
            import_report=self.import_report)

    def restore(self,d):
        self.config=Config06(**d['config']);self.brain=Brain06.from_dict(d['brain'])
        self.calibrator=Calibrator.from_dict(d['calibrator'])
        for k in ('session','plan','position','split_report','splits','test_opened','cal_scores','cal_labels','last','metrics','import_report'):
            setattr(self,k,d.get(k))

    def save(self,records=()):
        with self.db:
            for r in records:self.db.execute('INSERT INTO records VALUES (?,?,?,?)',
                (self.session,r['step'],r['phase'],canonical(r)))
            self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(canonical(self.checkpoint()),))

    def import_lines(self,lines,origin='upload'):
        if self.auto:raise ValueError('Mettre l\'apprentissage en pause avant import')
        counts=Counter();sha=hashlib.sha256();total=0;valid=0;liquid=0;quotes=0
        # Stream records, corpus grows on disk; 100 MiB guard for uploads is in API.
        with self.db:
            for line in lines:
                if isinstance(line,bytes):line=line.decode('utf-8-sig')
                if not line.strip():continue
                total+=1;sha.update(line.encode())
                try:
                    row=json.loads(line,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('non_fini')))
                    ep,v=validate_row(row)
                    if self.expected_source and ep.source!=self.expected_source:
                        raise ValueError('source_incompatible_attendue_'+self.expected_source)
                    payload=canonical(v)
                    prior=self.db.execute('SELECT payload FROM corpus WHERE uid=?',(ep.uid,)).fetchone()
                    if prior:
                        if prior[0]!=payload:raise ValueError('doublon_contradictoire')
                        counts['doublon']+=1;continue
                    self.db.execute('INSERT INTO corpus VALUES (?,?,?)',(ep.uid,ep.placed,payload))
                    valid+=1;liquid+=int(v['book'] is not None);quotes+=int(v['quote'] is not None)
                except (ValueError,TypeError,KeyError,IndexError,OverflowError) as e:
                    counts[str(e)[:80] if isinstance(e,ValueError) else 'schema_invalide']+=1
        self.import_report=dict(origin=origin,at=stamp(),sha256=sha.hexdigest(),lines=total,added=valid,
                                with_liquidity=liquid,with_delayed_quotes=quotes,rejected=dict(counts))
        self.save();return self.import_report

    def import_live(self,run_db):
        source=sqlite3.connect(f'file:{Path(run_db).absolute()}?mode=ro',uri=True)
        try:return self.import_lines((r[0] for r in source.execute('SELECT payload FROM opportunities ORDER BY id')),'collecte06')
        finally:source.close()

    def create(self,config):
        if self.auto:raise ValueError('Mettre l\'apprentissage en pause')
        cursor=self.db.execute('SELECT payload FROM corpus ORDER BY placed DESC LIMIT ?', (config.max_windows,))
        eps=[];corpus_digest=hashlib.sha256()
        for raw, in cursor:
            v=json.loads(raw)
            if config.use_liquidity and not v.get('book'):continue
            # Full tick lists remain on disk; split needs only support and outcomes.
            ep=Episode.from_dict(v['episode'])
            if self.expected_source and ep.source!=self.expected_source: continue
            eps.append(replace(ep,ticks=()))
            corpus_digest.update(raw.encode());corpus_digest.update(b'\n')
        splits,report=split_chronological(eps)
        report['fingerprint']=corpus_digest.hexdigest()
        rng=np.random.default_rng(config.seed);plan=[]
        def add(name,rows,shuffle):
            ids=[e.uid for e in rows]
            if shuffle:rng.shuffle(ids)
            plan.extend([name,i] for i in ids)
        if config.mode in ('curriculum','balanced'):
            pools=[[e for e in splits['train'] if e.category==c] for c in ('hausse','stable','baisse')]
            n=min(map(len,pools))
            if not n:raise ValueError('Une classe exclusive manque. Collecter plus ou choisir chronologique')
            for i in range(config.epochs):
                chosen=[pool[int(j)] for pool in pools for j in rng.choice(len(pool),n,replace=False)]
                add('A equilibre '+str(i+1), sorted(chosen,key=lambda e:e.placed),config.shuffle_train)
            if config.mode=='curriculum':
                add('B ambigu', [e for e in splits['train'] if sum(e.touches)!=1],config.shuffle_train)
                add('C naturel', splits['train'],config.shuffle_train)
        else:
            for i in range(config.epochs):add('Train '+str(i+1),splits['train'],config.shuffle_train)
        add('Calibration gelee',splits['validation'],False)
        if len(plan)>250000:raise ValueError('Plus de 250000 presentations : reduire passages ou corpus')
        old=copy.deepcopy(self.checkpoint())
        if self.session:
            with self.db:self.db.execute('INSERT OR REPLACE INTO archives VALUES (?,?)',(self.session,canonical(old)))
        self.config=config;self.brain=Brain06(seed=config.seed,n_kc=config.n_kc,sparsity=config.sparsity,use_liquidity=config.use_liquidity)
        self.calibrator=Calibrator(min_total=60,min_bin=20);self.plan=plan;self.position=0
        self.session=str(time.time_ns());self.splits={k:[e.uid for e in v] for k,v in splits.items()}
        self.split_report=report;self.test_opened=False;self.cal_scores=[];self.cal_labels=[]
        self.last=None;self.last_decision=None;self.metrics={};self.error=None
        try:self.save()
        except Exception:self.restore(old);raise
        return self.snapshot()

    def step_batch(self,n=None):
        if not self.session:raise ValueError('Creer un protocole avant de demarrer')
        if self.position>=len(self.plan):self.auto=False;return
        old=copy.deepcopy(self.checkpoint());output=[]
        try:
            for _ in range(min(n or self.config.batch,len(self.plan)-self.position)):
                phase,uid=self.plan[self.position]
                v=json.loads(self.db.execute('SELECT payload FROM corpus WHERE uid=?',(uid,)).fetchone()[0])
                ep=Episode.from_dict(v['episode']);training=phase not in ('Calibration gelee','Test gele')
                before=self.brain.fingerprint()
                d=self.brain.predict(ep.observation(),v['book'],self.config.epsilon if training else 0)
                probs=self.calibrator.predict(d.scores,before) if phase=='Test gele' else None
                update=None;value=None
                if training:
                    update=self.brain.learn_outcomes(d,ep.touches,self.config.learning_rate,self.config.signal)
                    if self.config.economic_head and v['quote']:
                        m=v['quote']['effective_gross'];cost=v['quote'].get('cost_per_stake',0)
                        targets=[net_result(ep.touches[a],m[a],1.,cost) if m[a] is not None and (self.config.signal=='all' or a==d.action) else None for a in range(3)]
                        value=self.brain.learn_financial(d,targets,rate=self.config.learning_rate*.4)
                elif phase=='Calibration gelee':
                    self.cal_scores.append(d.scores.tolist());self.cal_labels.append(list(ep.touches))
                self.position+=1
                if phase=='Calibration gelee' and (self.position==len(self.plan) or self.plan[self.position][0]!='Calibration gelee'):
                    self.calibrator.fit(self.cal_scores,self.cal_labels,self.brain.fingerprint())
                if not training and self.brain.fingerprint()!=before:raise RuntimeError('Poids modifies pendant evaluation gelee')
                rec=dict(step=self.position,phase=phase,uid=uid,placed=ep.placed,category=ep.category,
                    chosen=d.action,scores=d.scores.tolist(),touches=list(ep.touches),probabilities=probs,
                    win=bool(ep.touches[d.action]),updated=training,value_updated=value is not None,
                    economic_values=d.extra.get('economic_values'),active_kc=int(np.count_nonzero(d.kc)),
                    update=update,financial_update=value)
                output.append(rec);self.last=rec;self.last_decision=d
                m=self.metrics.setdefault(phase,dict(n=0,wins=0,always=[0,0,0],choices=[0,0,0],value_updates=0))
                m['n']+=1;m['wins']+=int(rec['win']);m['choices'][d.action]+=1
                for a in range(3):m['always'][a]+=int(ep.touches[a])
                m['value_updates']+=int(value is not None)
            self.save(output)
        except Exception as e:
            self.restore(old);self.auto=False;self.error=str(e);raise
        if self.position>=len(self.plan):self.auto=False

    def open_test(self):
        if self.auto or self.position!=len(self.plan) or not self.session:raise ValueError('Terminer le protocole')
        if self.test_opened:raise ValueError('Test deja ouvert. Utiliser des donnees nouvelles pour un nouveau test')
        self.plan.extend(['Test gele',i] for i in self.splits['test']);self.test_opened=True;self.save()

    def corpus_info(self):
        n,lo,hi=self.db.execute('SELECT COUNT(*),MIN(placed),MAX(placed) FROM corpus').fetchone()
        return dict(windows=n,first=lo,last=hi)

    def snapshot(self):
        report=copy.deepcopy(self.split_report)
        if report:
            report.pop('manifest',None)
            if not self.test_opened:report['splits']['test']={'n':report['splits']['test']['n'],'hidden':True}
        return dict(version='0.7.0-alpha',config=self.config.model_dump(),session=self.session,
            position=self.position,total=len(self.plan),auto=self.auto,test_opened=self.test_opened,
            done=bool(self.session) and self.position==len(self.plan),split=report,metrics=copy.deepcopy(self.metrics),
            last=copy.deepcopy(self.last),neural=self.brain.neural(self.last_decision,self.last.get('update') if self.last else None),corpus=self.corpus_info(),
            imported=self.import_report,error=self.error,calibration=dict(n=self.calibrator.n,min_total=self.calibrator.min_total))

    def report_header(self):
        d=self.snapshot();d['calibrator']=self.calibrator.to_dict();d['fingerprint']=self.brain.fingerprint()
        d['warning']='Test gele; multi-touch. Aucune affirmation de rentabilite Euphoria. Evaluation financiere seulement en simulation avec execution horodatee.'
        return d

    def report(self):
        d=self.report_header()
        d['records']=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM records WHERE session=? ORDER BY step',(self.session,))]
        return d

    def deployable(self):
        if self.auto or not self.session or self.position!=len(self.plan):raise ValueError('Terminer le protocole avant le transfert')
        return self.brain,self.calibrator,dict(session=self.session,at=stamp(),dataset=self.split_report['fingerprint'],
            config=self.config.model_dump(),warning='Modele gele; cotations simulees non validees par Euphoria')

    def close(self):self.db.close()
