"""Explicit training lifecycle. Mathematical updates are inherited unchanged.

Replay = original train plan, fresh weights. Continue = saved TRAIN partition,
retained weights, a fresh calibration. Test exposure is never silently erased.
"""
from __future__ import annotations
import copy
import hashlib
import json
import time
import uuid
from dataclasses import replace
from pathlib import Path
import numpy as np
from .academy06 import Academy06, Config06
from .brain06 import Brain06
from .dataset import Episode, canonical, split_chronological
from .economics import Calibrator
from .engine import stamp

CAL = 'Calibration gelee'
TEST = 'Test gele'

class Academy08(Academy06):
    def __init__(self, data, expected_source=None):
        self.lineage = {'mode': 'initial', 'parent': None, 'initial_updates': 0}
        self.benchmark_id=str(uuid.uuid4())
        self._usage = None
        super().__init__(data, expected_source)
        self.db.execute('CREATE TABLE IF NOT EXISTS exposed_test(uid TEXT PRIMARY KEY, session TEXT, dataset_id TEXT, benchmark_id TEXT, opened_at TEXT)')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(exposed_test)')}
        for name in ('dataset_id','benchmark_id','opened_at'):
            if name not in columns:self.db.execute('ALTER TABLE exposed_test ADD COLUMN '+name+' TEXT')
        # Backfill existing sessions: opening an old test is still an exposure.
        with self.db:
            if self.test_opened and self.splits:
                self._mark_seen(self.splits.get('test', []), self.session,getattr(self.config,'dataset_id',None),self.benchmark_id)
            for (raw,) in self.db.execute('SELECT payload FROM archives'):
                d = json.loads(raw)
                if d.get('test_opened') and d.get('splits'):
                    self._mark_seen(d['splits'].get('test', []),d.get('session'),d.get('config',{}).get('dataset_id'),d.get('benchmark_id'))
        self.refresh_usage()

    def checkpoint(self):
        d = super().checkpoint()
        d.update(version='0.8.0-alpha', lineage=copy.deepcopy(self.lineage),benchmark_id=self.benchmark_id)
        return d

    def restore(self, d):
        super().restore(d)
        self.lineage = copy.deepcopy(d.get('lineage') or {
            'mode': 'legacy', 'parent': None, 'initial_updates': 0})
        self.benchmark_id=d.get('benchmark_id') or str(uuid.uuid5(uuid.NAMESPACE_URL,'flytrade-legacy-benchmark:'+str(d.get('session'))))
        self.last_decision = None

    def refresh_usage(self):
        info = dict(total=0, liquid=0, quotes=0, source={})
        for (raw,) in self.db.execute('SELECT payload FROM corpus'):
            v=json.loads(raw);info['total']+=1
            info['liquid']+=int(bool(v.get('book')))
            info['quotes']+=int(bool(v.get('quote')))
            source=v['episode']['source'];info['source'][source]=info['source'].get(source,0)+1
        self._usage=info

    def import_lines(self, lines, origin='upload', name=None):
        result=super().import_lines(lines, origin, name)
        self.refresh_usage()
        return result

    def _mark_seen(self,ids,session,dataset_id=None,benchmark_id=None):
        self.db.executemany('INSERT OR IGNORE INTO exposed_test(uid,session,dataset_id,benchmark_id,opened_at) VALUES (?,?,?,?,?)',
                            [(uid,str(session),dataset_id,benchmark_id,stamp()) for uid in ids])

    def _seen(self, ids):
        seen={r[0] for r in self.db.execute('SELECT uid FROM exposed_test')}
        return sum(uid in seen for uid in ids)

    def _build(self, config):
        dataset_id=config.dataset_id or self._default_dataset_id()
        info=self.db.execute('SELECT source,allow_exposed_training FROM datasets WHERE dataset_id=?',(dataset_id,)).fetchone()
        if not info:raise ValueError('Dataset introuvable')
        if self.expected_source and info[0]!=self.expected_source:raise ValueError('Dataset incompatible avec la source active')
        config=config.model_copy(update={'dataset_id':dataset_id,'economic_head':False})
        eps=[];digest=hashlib.sha256();considered=0
        cursor=self.db.execute('SELECT c.payload FROM dataset_windows d JOIN corpus c ON c.uid=d.uid WHERE d.dataset_id=? ORDER BY d.position DESC LIMIT ?', (dataset_id,config.max_windows))
        for (raw,) in cursor:
            considered+=1;v=json.loads(raw)
            if config.use_liquidity and not v.get('book'):continue
            ep=Episode.from_dict(v['episode'])
            if self.expected_source and ep.source!=self.expected_source:continue
            if sum(ep.touches)!=1:continue
            eps.append(replace(ep,ticks=()))
            digest.update(raw.encode());digest.update(b'\n')
        splits,report=split_chronological(eps)
        exposed_train=self._seen([e.uid for e in splits['train']])
        if exposed_train and not info[1]:
            raise ValueError('Des exemples TEST exposes entreraient dans TRAIN. Creer explicitement une nouvelle version de dataset.')
        report['previously_exposed_train']=exposed_train
        report['benchmark_contaminated']=bool(exposed_train)
        report['fingerprint']=digest.hexdigest()
        rng=np.random.default_rng(config.seed);plan=[]
        def add(phase, rows):
            ids=[e.uid for e in rows]
            if config.shuffle_train:rng.shuffle(ids)
            plan.extend([phase, uid] for uid in ids)
        if config.mode in ('curriculum','balanced'):
            pools=[[e for e in splits['train'] if e.category==c] for c in ('hausse','stable','baisse')]
            n=min(map(len,pools))
            if not n:raise ValueError('Une direction exclusive manque dans TRAIN. Collecter davantage ou choisir le parcours naturel. Rien n\'a ete reinitialise.')
            for i in range(config.epochs):
                rows=[pool[int(j)] for pool in pools for j in rng.choice(len(pool),n,replace=False)]
                add('A equilibre '+str(i+1),sorted(rows,key=lambda e:e.placed))
            if config.mode=='curriculum':
                add('B ambigu',[e for e in splits['train'] if sum(e.touches)!=1])
                add('C naturel',splits['train'])
        else:
            for i in range(config.epochs):add('Train '+str(i+1),splits['train'])
        plan.extend([CAL,e.uid] for e in splits['validation'])
        if len(plan)>250000:raise ValueError('Plus de 250000 presentations. Reduire les passages ou les donnees.')
        return config,plan,{k:[e.uid for e in v] for k,v in splits.items()},report,considered,len(eps)

    def preview(self, config):
        config,plan,splits,report,n,eligible=self._build(config)
        # Never reveal outcome counts of TEST through a preflight response.
        safe=copy.deepcopy(report);safe.pop('manifest',None)
        safe['splits']['test']={'n':len(splits['test']), 'hidden':True}
        return dict(ready=True, considered=n, eligible=eligible, filtered=n-eligible,
            presentations=len(plan), train_presentations=sum(p[0]!=CAL for p in plan),
            split=safe, calibration_min=60, calibration_n=len(splits['validation']),
            reused_test=self._seen(splits['test']), reused_in_train=self._seen(splits['train']),
            note='Verification uniquement : aucun poids, aucune session ni donnee modifies.')

    def _archive_insert(self, old):
        key=old.get('session') or 'initial-'+str(time.time_ns())
        self.db.execute('INSERT OR REPLACE INTO archives VALUES (?,?)',(key,canonical(old)))
        return key

    def _commit_new(self, old):
        try:
            with self.db:
                self._archive_insert(old)
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(canonical(self.checkpoint()),))
        except Exception:
            self.restore(old);self.auto=False
            raise

    def _start_session(self,config,plan,splits,report,mode,retain=False,benchmark_id=None):
        old=copy.deepcopy(self.checkpoint())
        if not retain:
            self.brain=Brain06(seed=config.seed,n_kc=config.n_kc,
                              sparsity=config.sparsity,use_liquidity=config.use_liquidity)
        self.lineage=dict(mode=mode,parent=old.get('session'),created_at=stamp(),
                          initial_updates=self.brain.updates, source=self.expected_source)
        self.benchmark_id=benchmark_id or str(uuid.uuid4())
        self.config=config;self.plan=copy.deepcopy(plan);self.splits=copy.deepcopy(splits)
        self.split_report=copy.deepcopy(report);self.session=str(time.time_ns());self.position=0
        self.calibrator=Calibrator(min_total=60,min_bin=20)
        self.test_opened=bool(self._seen(splits['test']));self.cal_scores=[];self.cal_labels=[];self.metrics={}
        self.last=None;self.last_decision=None;self.error=None;self.auto=False
        self._commit_new(old)
        return self.snapshot()

    def create(self, config):
        if self.auto:raise ValueError('Mettre l\'atelier en pause avant de preparer une autre experience.')
        config,plan,splits,report,_,_=self._build(config)
        return self._start_session(config,plan,splits,report,'fresh')

    def replay(self):
        if self.auto or not self.session:raise ValueError('Mettre en pause une experience existante avant de la rejouer.')
        plan=[p for p in self.plan if p[0]!=TEST]
        self._check_ids(plan)
        return self._start_session(self.config,plan,self.splits,self.split_report,'replay',benchmark_id=self.benchmark_id)

    def continue_training(self, passes=1):
        if self.auto or not self.session or self.position!=len(self.plan):
            raise ValueError('Terminer l\'experience courante avant d\'ajouter des passages. En pause au milieu : utiliser Reprendre.')
        if not 1<=passes<=20:raise ValueError('Choisir de 1 a 20 passages.')
        rng=np.random.default_rng(self.config.seed);plan=[]
        for i in range(passes):
            ids=list(self.splits['train'])
            if self.config.shuffle_train:rng.shuffle(ids)
            plan.extend(['Suite TRAIN '+str(i+1),uid] for uid in ids)
        plan.extend([CAL,uid] for uid in self.splits['validation'])
        if len(plan)>250000:raise ValueError('Plan trop grand; reduire les passages.')
        self._check_ids(plan)
        config=self.config.model_copy(update={'mode':'chronological','epochs':passes})
        return self._start_session(config,plan,self.splits,self.split_report,'continue',retain=True,benchmark_id=self.benchmark_id)

    def _check_ids(self, plan):
        existing={r[0] for r in self.db.execute('SELECT uid FROM corpus')}
        if any(uid not in existing for _,uid in plan):raise ValueError('Un exemple du protocole manque au corpus. Aucune modification effectuee.')

    def reset_training(self, config):
        if self.auto:raise ValueError('Mettre l\'atelier en pause avant de recreer un cerveau.')
        old=copy.deepcopy(self.checkpoint())
        self.brain=Brain06(seed=config.seed,n_kc=config.n_kc,sparsity=config.sparsity,use_liquidity=config.use_liquidity)
        self.config=config;self.calibrator=Calibrator(min_total=60,min_bin=20)
        self.lineage=dict(mode='initial',parent=old.get('session'),initial_updates=0,created_at=stamp())
        self.benchmark_id=str(uuid.uuid4())
        self.session=None;self.plan=[];self.position=0;self.splits=None;self.split_report=None
        self.auto=False;self.error=None;self.test_opened=False;self.cal_scores=[];self.cal_labels=[]
        self.last=None;self.last_decision=None;self.metrics={}
        self._commit_new(old)
        return self.snapshot()

    def open_test(self):
        # The exposure log survives all resets/replays; outcomes themselves remain hidden until test.
        old=copy.deepcopy(self.checkpoint())
        try:
            if self.auto or not self.session or self.position!=len(self.plan):raise ValueError('Terminer le protocole avant le test.')
            if self.test_opened or self._seen(self.splits['test']):raise ValueError('Cette partition TEST est deja exposee et reste brulee.')
            self.plan.extend([TEST,uid] for uid in self.splits['test']);self.test_opened=True
            with self.db:
                self._mark_seen(self.splits['test'],self.session,self.config.dataset_id,self.benchmark_id)
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(canonical(self.checkpoint()),))
        except Exception:
            self.restore(old);raise

    def snapshot(self):
        d=super().snapshot();d['version']='0.8.0-alpha'
        d['lineage']=copy.deepcopy(self.lineage)
        d['weights_updates']=self.brain.updates;d['value_updates']=self.brain.value_updates
        d['brain_id']=self.brain.brain_id
        d['weight_fingerprint']=self.brain.fingerprint()
        d['corpus']['quality']=copy.deepcopy(self._usage)
        d['next_phase']=self.plan[self.position][0] if self.position<len(self.plan) else None
        d['test_reused']=self._seen(self.splits['test']) if self.splits and self._usage is not None else 0
        d['train_previously_tested']=self._seen(self.splits['train']) if self.splits and self._usage is not None else 0
        return d

    def archive_list(self):
        result=[]
        for key,raw in self.db.execute('SELECT session,payload FROM archives ORDER BY rowid DESC LIMIT 12'):
            d=json.loads(raw)
            result.append(dict(id=key,session=d.get('session'),config=d['config'],
                position=d.get('position',0),total=len(d.get('plan',[])),updates=d['brain']['updates'],
                mode=d.get('lineage',{}).get('mode','legacy'),test_opened=d.get('test_opened',False)))
        return result

    def archive(self, key):
        r=self.db.execute('SELECT payload FROM archives WHERE session=?',(key,)).fetchone()
        if not r:raise ValueError('Archive introuvable.')
        d=json.loads(r[0]);test_ids=(d.get('splits') or {}).get('test',[])
        if not test_ids or self._seen(test_ids):return d
        safe=copy.deepcopy(d);safe['splits']['test']=[]
        report=safe.get('split_report')
        if report:
            report.pop('manifest',None);report['splits']['test']={'n':len(test_ids),'hidden':True}
        safe['metrics']={k:v for k,v in safe.get('metrics',{}).items() if k!=TEST}
        safe['test_redacted']=True
        return safe
