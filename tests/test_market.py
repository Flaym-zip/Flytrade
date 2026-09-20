import asyncio
import json
import time
from datetime import datetime, timezone

import pytest
from websockets.asyncio.server import serve

from app.market import Market, decode_message, stream


def feed(m, i, price=3000.2):
    return m.receive({'kind':'trade','time':1000+i,'price':price,'id':100+i}, now=100+i)


def warm(m):
    m.begin(now=100)
    for i in range(33):feed(m,i)
    return 132


def test_match_parser_and_heartbeat():
    raw={'type':'match','product_id':'ETH-USD','price':'3000.25','trade_id':123,
         'time':'2026-09-19T12:00:00.123456Z'}
    tick=decode_message(json.dumps(raw))[0]
    assert tick['price']==3000.25 and tick['id']==123 and not tick['snapshot']
    assert decode_message(json.dumps({**raw,'type':'last_match'}))[0]['snapshot']
    hb={'type':'heartbeat','product_id':'ETH-USD','last_trade_id':123,'time':raw['time']}
    assert decode_message(json.dumps(hb))[0]['kind']=='clock'
    assert decode_message(json.dumps({**raw,'product_id':'BTC-USD'}))==[]
    assert decode_message('{"type":"subscriptions"}')==[]


@pytest.mark.parametrize('price',['NaN','Infinity','-2','0','10000000000'])
def test_bad_prices_rejected(price):
    with pytest.raises(ValueError):decode_message(json.dumps({'type':'match','product_id':'ETH-USD','price':price,'trade_id':1,'time':'2026-09-19T12:00:00Z'}))


def test_server_error_rejected():
    with pytest.raises(ValueError):decode_message('{"type":"error","message":"bad subscription"}')


def test_warmup_and_causal_samples():
    m=Market();m.begin(now=100)
    for i in range(29):feed(m,i,3000+i*.01)
    assert m.ready_reason(128) is not None
    for i in range(29,33):feed(m,i,3000+i*.01)
    assert m.ready_reason(132) is None
    history=m.history()
    assert len(history)==31 and history[-1]==3000.32
    m.receive({'kind':'trade','time':1032.6,'id':133,'price':3010},now=132.6)
    assert m.history()==history  # No lookahead into the current incomplete second.
    assert m.ready_reason(136) is not None


def test_duplicate_not_a_new_tick():
    m=Market();warm(m)
    count=m.total_ticks
    assert feed(m,32) is None and m.total_ticks==count


def test_trade_gap_and_heartbeat_gap_invalidate_history():
    m=Market();warm(m)
    epoch=m.epoch
    reason=m.receive({'kind':'trade','time':1033,'price':3000,'id':134},now=133)
    assert reason and m.gaps==1 and m.epoch>epoch and m.ready_reason(133)
    assert m.receive({'kind':'clock','time':1034,'id':136},now=134)
    assert m.ready_reason(134) is not None


def test_heartbeat_does_not_invent_prices():
    m=Market();warm(m);count=m.total_ticks
    m.receive({'kind':'clock','time':1033,'id':132},now=133)
    assert m.total_ticks==count and m.last_trade_time==1032
    assert m.watermark==1033


def test_old_and_out_of_order_trade_invalidates_epoch():
    m=Market();warm(m)
    assert m.receive({'kind':'trade','time':1031,'id':133,'price':3000},now=132.1)


def test_stale_or_disconnect_freezes_view():
    m=Market();warm(m)
    assert m.estimated_time(160)==1035
    m.disconnect('test')
    assert m.estimated_time(160)==1032 and not m.transport_healthy(132)


def test_chart_keeps_peak_and_trough():
    m=Market();m.begin(now=100)
    for i,(t,p) in enumerate([(1000,3000),(1000.02,3002),(1000.04,2999),(1000.06,3001)]):
        m.receive({'kind':'trade','time':t,'price':p,'id':i},now=t-900)
    assert [x[1] for x in m.chart(100.06)]==[3000,3002,2999,3001]


def test_real_websocket_client_against_local_mock():
    # This checks the actual WebSocket implementation, not Coinbase access.
    async def exercise():
        seen=[];sub=[];done=asyncio.Event();errors=[]
        async def handler(ws):
            sub.append(json.loads(await ws.recv()))
            t=datetime.now(timezone.utc).isoformat()
            await ws.send(json.dumps({'type':'match','product_id':'ETH-USD','time':t,'price':'3000.12','trade_id':1}))
            await ws.send(json.dumps({'type':'heartbeat','product_id':'ETH-USD','time':t,'last_trade_id':1}))
            await done.wait()
        async def connected():seen.append('connect')
        async def event(e):
            seen.append(e['kind'])
            if e['kind']=='clock':done.set()
        async def error(e):errors.append(e)
        async with serve(handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            task=asyncio.create_task(stream(connected,event,error,url=f'ws://127.0.0.1:{port}'))
            await asyncio.wait_for(done.wait(),5)
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        assert seen[:3]==['connect','trade','clock']
        assert sub[0]['channels']==['matches','heartbeat'] and sub[0]['product_ids']==['ETH-USD']
    asyncio.run(exercise())
