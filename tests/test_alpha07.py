"""Public-feed protocol, causal isolation and local Wiki regression tests.
Network tests use localhost servers or mock HTTP, never a live exchange.
"""
import asyncio
import copy
import json
import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve
from app import feed07 as f
from app.academy06 import Academy06, Config06
from app.economy06 import Rules06
from app.wiki07 import wiki_parameters
from app.run06 import Run06
from app.orderbook import OrderBook

ROOT=Path(__file__).resolve().parents[1]
REST={'error':[], 'result':{'XETHZUSD':{'c':['3000.20','1'],'b':['3000.1','1'],'a':['3000.3','1']}}}

def iso(t=None):return datetime.fromtimestamp(time.time() if t is None else t,timezone.utc).isoformat().replace('+00:00','Z')

def trade(t=None,tid=20,side='buy',symbol='ETH/USD'):
    return dict(channel='trade',type='update',data=[dict(symbol=symbol,side=side,qty=2.,price=3000.2,trade_id=tid,timestamp=iso(t))])

def book_payload(bids=None,asks=None):
    bids=bids or {'2999.90':'2.0000','2998.00':'1.0000'}
    asks=asks or {'3000.10':'1.0000','3002.00':'2.0000'}
    b={Decimal(p):Decimal(q) for p,q in bids.items()};a={Decimal(p):Decimal(q) for p,q in asks.items()}
    return dict(channel='book',type='snapshot',data=[dict(symbol='ETH/USD',bids=[dict(price=p,qty=q) for p,q in bids.items()],asks=[dict(price=p,qty=q) for p,q in asks.items()],checksum=f.crc_book(b,a))])

def test_rest_is_diagnostic_not_trade():
    m=f.KrakenMarket();m.begin(now=100)
    for i in range(3):m.watch.success(REST,100+i*2,now=100+i*2+.1)
    assert m.total_ticks==0 and not m.ticks and m.ready_reason(105)
    d=m.watch.snapshot(104.2)
    assert d['healthy'] and d['last_price']==3000.2 and d['successes']==3
    assert d['trade_timestamp'] is None and not d['settlement_eligible']
    assert d['max_success_gap_s']==pytest.approx(2)
    assert d['p95_latency_ms']==pytest.approx(100)
    assert not m.watch.snapshot(108)['healthy']

def test_rest_gap_counts_success_intervals_and_keeps_error_visible():
    w=f.HttpWatch();w.success(REST,1,now=1.1);w.failure(ValueError('offline'))
    w.success(REST,5,now=5.2)
    assert w.snapshot(6)['success_gaps_over_3s']==1 and w.failures==1 and w.last_error is None

@pytest.mark.parametrize('payload',[
    {},{'error':['EAPI:Rate limit exceeded']},{'error':[],'result':{'XXBTZUSD':REST['result']['XETHZUSD']}},
    {'result':{'XETHZUSD':{'a':['2'],'b':['3'],'c':['2.5']}}},
    {'result':{'XETHZUSD':{'a':['3'],'b':['2'],'c':['NaN']}}},
])
def test_rest_bad_payload_does_not_refresh(payload):
    w=f.HttpWatch()
    with pytest.raises((ValueError,KeyError,TypeError)):w.success(payload,100,now=101)
    assert w.last_success is None and w.successes==0

def test_rest_poller_performs_real_http_semantics_and_waits(monkeypatch):
    async def scenario():
        times=[]
        async def handler(req):times.append(time.monotonic());return httpx.Response(200,json=REST)
        w=f.HttpWatch()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await f.poll_rest(w,client=client,stop_after=3)
        assert w.attempts==w.successes==3 and w.failures==0
        assert all(.025<=b-a<.5 for a,b in zip(times,times[1:]))
    monkeypatch.setattr(f,'POLL_SECONDS',.04)
    asyncio.run(scenario())

@pytest.mark.parametrize('status,headers,payload',[(429,{'Retry-After':'12'},{'error':[]}),
    (503,{'Retry-After':'Sun, 20 Sep 2037 08:00:00 GMT'},{}),
    (200,{}, {'error':['EAPI:Rate limit exceeded']})])
def test_throttled_request_obeys_cooldown(status,headers,payload):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(status,headers=headers,json=payload))) as client:
            w=f.HttpWatch();await f.poll_rest(w,client=client,stop_after=1)
            assert w.failures==1 and w.successes==0 and w.cooldown_until>time.monotonic()+4
    asyncio.run(scenario())

def test_poller_timeout_and_cancellation(monkeypatch):
    async def scenario():
        async def slow(req):await asyncio.sleep(10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            w=f.HttpWatch();await f.poll_rest(w,client=client,stop_after=1)
            assert w.failures==1 and 'Timeout' in w.last_error
            task=asyncio.create_task(f.poll_rest(w,client=client));await asyncio.sleep(.005);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
    monkeypatch.setattr(f,'HTTP_TIMEOUT',.02)
    asyncio.run(scenario())

@pytest.mark.parametrize('side,expected',[('buy','sell'),('sell','buy')])
def test_trade_side_is_taker_in_kraken_and_maker_in_internal_flow(side,expected):
    e=f.trade_events(trade(side=side))[0]
    assert e['side']==expected
    b=f.KrakenBook();b.trade(e)
    assert b.trades[-1][2]==(1 if side=='buy' else -1)

def test_trade_snapshot_seeds_only_latest_and_no_false_warmup():
    d=trade(1000,tid=20);d['type']='snapshot';d['data']+=trade(998,tid=18)['data']+trade(999,tid=19)['data']
    es=f.trade_events(d);assert len(es)==1 and es[0]['id']==20
    m=f.KrakenMarket();m.begin(now=100);m.receive(es[0],now=100)
    assert m.ready_reason(100) is not None and m.total_ticks==1
    m.receive(es[0],now=100.1);assert m.total_ticks==1
    reason=m.receive(f.trade_events(trade(1002,tid=22))[0],now=102)
    assert 'trade_id' in reason and m.gaps==1

def test_pong_healthy_transport_not_fresh_trade():
    m=f.KrakenMarket();m.begin(now=100)
    m.receive(f.trade_events(trade(1000))[0],now=100)
    for i in range(1,8):m.receive(dict(kind='clock',time=1000+i,id=0),now=100+i)
    assert m.transport_healthy(107) and m.gaps==0 and m.total_ticks==1
    assert 'prix trop ancien' in m.ready_reason(107)

@pytest.mark.parametrize('d',[{'success':False,'error':'bad symbol'}, {'channel':'status','data':[{'system':'maintenance'}]},
    {'channel':'trade','type':'update','data':None}])
def test_bad_or_unavailable_feed(d):
    with pytest.raises(ValueError):f.trade_events(d)

def test_official_kraken_checksum_fixture():
    # Source: official Book checksum (WebSocket v2), BTC example. Decimal lexical precision retained.
    b=[('45283.5','0.10000000'),('45283.4','1.54582015'),('45282.1','0.10000000'),('45281.0','0.10000000'),('45280.3','1.54592586'),('45279.0','0.07990000'),('45277.6','0.03310103'),('45277.5','0.30000000'),('45277.3','1.54602737'),('45276.6','0.15445238')]
    a=[('45285.2','0.00100000'),('45286.4','1.54571953'),('45286.6','1.54571109'),('45289.6','1.54560911'),('45290.2','0.15890660'),('45291.8','1.54553491'),('45294.7','0.04454749'),('45296.1','0.35380000'),('45297.5','0.09945542'),('45299.5','0.18772827')]
    assert f.crc_book(dict((Decimal(p),Decimal(q)) for p,q in b),dict((Decimal(p),Decimal(q)) for p,q in a))==3310070434

def test_book_snapshot_and_absolute_update_and_delete():
    b=f.KrakenBook();b.begin();b.receive(book_payload())
    assert b.snapshot(time.time())['available'] and b.checksums==1
    d=book_payload();d['type']='update';r=d['data'][0]
    r['bids']=[{'price':'2999.90','qty':'0'},{'price':'2999.80','qty':'3.0000'}];r['asks']=[]
    expected={Decimal('2998.00'):Decimal('1.0000'),Decimal('2999.80'):Decimal('3.0000')}
    r['checksum']=f.crc_book(expected,b.dasks)
    b.receive(d);assert b.dbids==expected and b.checksums==2
    b.receive(d);assert b.dbids==expected  # quantities absolute, not added

def test_book_bad_checksum_immediately_unavailable():
    b=f.KrakenBook();b.begin();b.receive(book_payload());d=book_payload();d['data'][0]['checksum']=1
    with pytest.raises(ValueError,match='CRC32'):b.receive(d)
    assert not b.snapshot(time.time())['available']

def test_book_truncates_and_marks_incomplete_liquidity_band():
    bids={str(Decimal('2999.99')-Decimal('.001')*i):'1.0' for i in range(100)}
    asks={str(Decimal('3000.01')+Decimal('.001')*i):'1.0' for i in range(100)}
    b=f.KrakenBook();b.begin();b.receive(book_payload(bids,asks))
    assert b.snapshot_ok and not b.snapshot(time.time())['available'] and not b.coverage
    d=book_payload();d['type']='update';d['data'][0].update(bids=[{'price':'2999.995','qty':'2'}],asks=[])
    bb={**b.dbids,Decimal('2999.995'):Decimal('2')};bb=dict(sorted(bb.items(),reverse=True)[:100])
    d['data'][0]['checksum']=f.crc_book(bb,b.dasks);b.receive(d)
    assert len(b.dbids)==100 and max(b.dbids)==Decimal('2999.995')

def test_book_and_trade_stream_end_to_end_localhost(monkeypatch):
    async def scenario():
        seen=[];book=f.KrakenBook();trade_ready=asyncio.Event();book_ready=asyncio.Event();errors=[]
        async def handler(ws):
            async for raw in ws:
                d=json.loads(raw)
                if d['method']=='subscribe':
                    if d['params']['channel']=='trade':await ws.send(json.dumps(trade()))
                    else:await ws.send(json.dumps(book_payload()))
                if d['method']=='ping':await ws.send(json.dumps(dict(method='pong',req_id=d['req_id'],time_out=iso())))
        async def connected():seen.append('connected')
        async def receive(e):seen.append(e);trade_ready.set()
        async def bc():book.begin()
        async def br(d):book.receive(d);book_ready.set()
        async def error(e):errors.append(e)
        async with serve(handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1];url=f'ws://127.0.0.1:{port}'
            tasks=[asyncio.create_task(f.stream_kraken(connected,receive,error,url=url)),asyncio.create_task(f.stream_kraken_book(bc,br,error,url=url))]
            try:
                await asyncio.wait_for(asyncio.gather(trade_ready.wait(),book_ready.wait()),3)
                await asyncio.sleep(.06)
                assert any(isinstance(x,dict) and x['kind']=='clock' for x in seen)
                assert book.snapshot(time.time())['available'] and not errors
            finally:
                for t in tasks:t.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    monkeypatch.setattr(f,'PING_SECONDS',.02)
    asyncio.run(scenario())

def test_import_rejects_other_venue_not_merges_silently(tmp_path):
    from test_alpha06 import rows
    a=Academy06(tmp_path,expected_source='kraken-ETH-USD')
    data=list(rows(30))
    result=a.import_lines(json.dumps(r) for r in data)
    assert result['added']==0 and result['rejected']
    for r in data:r['source']='kraken-ETH-USD'
    result=a.import_lines(json.dumps(r) for r in data)
    assert result['added']==30
    a.close()

async def idle(*args,**kw):await asyncio.Event().wait()

def test_runtime_namespaces_preserve_old_wallet_and_add_wiki(tmp_path,monkeypatch):
    import app.main as main
    # Place a pre-existing Coinbase wallet in root.
    old=Run06(tmp_path,f.Market(),OrderBook());old.stats['net']=7;old.save();old.close()
    before=(tmp_path/'flytrade06.sqlite3').read_bytes()
    monkeypatch.setenv('FLYTRADE_DATA',str(tmp_path));monkeypatch.setenv('FLYTRADE_FEED','kraken')
    for name in ('stream_kraken','stream_kraken_book','poll_rest'):monkeypatch.setattr(main,name,idle)
    with TestClient(main.app) as c:
        state=c.get('/api/state').json();assert state['stats']['capital']==20 and state['market']['provider']=='kraken'
        assert c.get('/wiki').status_code==200
        p=c.get('/api/wiki/parameters').json();assert sum(len(g['items']) for g in p['groups'])==30
        assert c.get('/api/feed/diagnostics').json()['rest']['settlement_eligible'] is False
        assert 'default-src' in c.get('/wiki').headers['Content-Security-Policy']
    assert (tmp_path/'flytrade06.sqlite3').read_bytes()==before
    assert (tmp_path/'sources/kraken/flytrade06.sqlite3').exists()
    monkeypatch.setenv('FLYTRADE_FEED','coinbase')
    for name in ('stream','stream_book'):monkeypatch.setattr(main,name,idle)
    with TestClient(main.app) as c:assert c.get('/api/state').json()['stats']['capital']==27


def test_wiki_registry_covers_all_actual_settings():
    d=wiki_parameters();keys=[{i['key'] for i in group['items']} for group in d['groups']]
    assert keys==[set(Config06.model_fields),set(Rules06.model_fields)]
    for group,model in zip(d['groups'],[Config06,Rules06]):
        defaults=model().model_dump()
        for item in group['items']:assert defaults[item['key']]==item['default']


def test_wiki_has_21_chapters_and_no_broken_internal_anchors():
    from html.parser import HTMLParser
    class Parse(HTMLParser):
        def __init__(self):super().__init__();self.ids=[];self.links=[];self.scripts=[];self.count=0
        def handle_starttag(self,tag,attrs):
            d=dict(attrs)
            if 'id' in d:self.ids.append(d['id'])
            if tag=='a' and 'href' in d:self.links.append(d['href'])
            if tag=='script':self.scripts.append(d.get('src','inline'))
            if tag=='article':self.count+=1
    p=Parse();p.feed((ROOT/'app/static/wiki07.html').read_text())
    assert p.count==21 and len(p.ids)==len(set(p.ids))
    assert all(link[1:] in p.ids for link in p.links if link.startswith('#'))
    assert all(s.startswith('/static/') for s in p.scripts)
    d=wiki_parameters()
    for g in d['groups']:
        for i in g['items']:assert 'p-'+i['key'] in p.ids
    assert len((ROOT/'docs/wiki/WIKI-07.txt').read_text().split())>10000


def test_wiki_build_matches_runtime_code_hash():
    d=json.loads((ROOT/'app/static/wiki/parameters.json').read_text())
    assert d==wiki_parameters(), 'Regenerer le Wiki apres modification du moteur.'
