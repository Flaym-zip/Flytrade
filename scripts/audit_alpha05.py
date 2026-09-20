from pathlib import Path
import json,collections,math,hashlib
import numpy as np
import argparse
p=argparse.ArgumentParser(description='Audit reproductible du run alpha05 fourni')
p.add_argument('directory',type=Path,help='Dossier contenant observations et checkpoint alpha05')
root=p.parse_args().directory
rows=[json.loads(l) for l in (root/'flytrade-alpha05-observations.jsonl').read_text().splitlines() if l.strip()]
ck=json.loads((root/'flytrade-alpha05-live.json').read_text())
print('generations',collections.Counter(r['generation'] for r in rows))
r=[v for v in rows if v['generation']==ck['generation']];tr=[v for v in r if v['status'] in ('won','lost')]
print('CURRENT',len(r),collections.Counter(v['status'] for v in r),len(tr),'net',sum(v['net'] for v in tr))
for v in tr:
 print(v['id'],v['action'],v['status'],v['quotes']['gross'][v['chosen']],v['net'], 'sigma',np.std(np.diff(v['history'])),'p',v['probabilities'][v['chosen']])
issues=[]
for v in rows:
 if v['status']=='void':continue
 touches=[any(math.floor(float(p)/.5)==v['base_row']+shift for t,p,tid in v['window_ticks'] if v['start']<=t<v['end']) for shift in (1,0,-1)]
 if touches!=v['touches']:issues.append(['touches',v['id']])
 if v['chosen'] is not None:
  net=v['settings']['stake']*((v['quotes']['effective_gross'][v['chosen']] if touches[v['chosen']] else 0)-1-v['settings']['cost_per_stake'])
  if abs(net-v['net'])>1e-8:issues.append(['payoff',v['id']])
 if not v['start']-v['placed']>=10-1e-6:issues.append(['lead',v['id']])
print('issues',issues)
print('pnl excluding >=50',sum(v['net'] for v in tr if v['quotes']['gross'][v['chosen']]<50))
print('cap scenario',[(cap, sum(v['settings']['stake']*((min(cap,v['quotes']['gross'][v['chosen']])*(1-v['settings']['haircut']) if v['hit'] else 0)-1-v['settings']['cost_per_stake']) for v in tr)) for cap in [5,10,20,50,100]])
print('weights',ck['brain'].keys())
print('all',len(rows),sum(v['net'] or 0 for v in rows))
audit={'source_sha256':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in ('flytrade-alpha05-observations.jsonl','flytrade-alpha05-live.json')},'observations_all':len(rows),'all_status':dict(collections.Counter(v['status'] for v in rows)), 'generations':dict(collections.Counter(v['generation'] for v in rows)), 'checkpoint_generation':ck['generation'], 'generation_completed':len(r),'checkpoint_pending':len(ck['pending']),'status_current':dict(collections.Counter(v['status'] for v in r)),'funded_completed':len(tr),'wins':sum(v['hit'] for v in tr),'losses':sum(not v['hit'] for v in tr),'net_units_alpha05':sum(v['net'] for v in tr),'starting_units_alpha05':ck['settings']['initial_capital'],'arithmetic_issues':issues,'quote_sources':dict(collections.Counter(v['quotes']['source'] for v in r)),'trades':[{'id':v['id'],'action':v['action'],'status':v['status'],'gross':v['quotes']['gross'][v['chosen']],'effective_gross':v['quotes']['effective_gross'][v['chosen']],'stake':v['settings']['stake'],'net':v['net'],'p':v['probabilities'][v['chosen']],'asof':v['quotes']['asof'],'start':v['start'],'end':v['end']} for v in tr],'sensitivity_NOT_a_corrected_replay':{str(cap):sum(v['settings']['stake']*((min(cap,v['quotes']['gross'][v['chosen']])*(1-v['settings']['haircut']) if v['hit'] else 0)-1-v['settings']['cost_per_stake']) for v in tr) for cap in [5,10,20,50,100]},'calibration_n':ck['calibrator']['n'],'model_updates':ck['brain']['updates'],'uses_orderbook':ck['brain']['use_liquidity'],'limitations':['Displayed multipliers are SYNTHETIC, not Euphoria quotes.','No recorded execution delay or fill quotes: cannot reconstruct historical delayed executions.','Sensitivity fixes the same actions and outcomes; not a rerun of the policy.','Forty calibration windows; not a guarantee of conditional probabilities under extreme synthetic payouts.']}
(root/'flytrade-alpha06-audit-alpha05.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False))
