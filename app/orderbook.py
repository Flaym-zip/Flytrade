"""Read-only Coinbase L2 + trade flow. Independent of price settlement.
No key, order submission or wallet code. Missing/stale != neutral liquidity.
"""
from __future__ import annotations
import asyncio
import json
import math
import time
from collections import deque
from websockets.asyncio.client import connect
from .market import PRODUCT, URL, timestamp


class OrderBook:
    def __init__(self):
        self.bids, self.asks = {}, {}
        self.connected = False
        self.snapshot_ok = False
        self.last_recv = 0.0
        self.last_time = 0.0
        self.error = 'En attente du carnet Coinbase'
        self.trades = deque(maxlen=30000)
        self.flow_start = None

    def begin(self):
        self.bids.clear(); self.asks.clear()
        self.connected = True; self.snapshot_ok = False
        self.last_recv = time.monotonic(); self.last_time = 0.0
        self.error = None

    def disconnect(self, reason):
        self.connected = False; self.snapshot_ok = False
        self.error = str(reason)[:180]
        self.bids.clear(); self.asks.clear()

    def reset_flow(self):
        self.trades.clear(); self.flow_start = None

    def trade(self, event):
        if event.get('snapshot'): return
        size = event.get('size'); side = event.get('side')
        if size is None or side not in ('buy', 'sell'): return
        size = float(size)
        if not math.isfinite(size) or size < 0: return
        t = float(event['time'])
        # Coinbase match.side is the MAKER side: sell -> aggressive buyer.
        self.trades.append((t, size, 1 if side == 'sell' else -1))
        if self.flow_start is None: self.flow_start = t

    def receive(self, data, now=None):
        now = time.monotonic() if now is None else now
        if data.get('type') == 'error': raise ValueError(data.get('message', 'Erreur L2'))
        if data.get('product_id') != PRODUCT: return
        kind = data.get('type')
        if kind not in ('snapshot', 'l2update', 'heartbeat'): return
        if self.last_recv and now-self.last_recv > 3 and self.snapshot_ok:
            raise ValueError('Carnet interrompu : nouveau snapshot requis')
        if not self.connected: raise ValueError('Carnet deconnecte')
        if 'time' in data:
            t = timestamp(data['time'])
            if t < self.last_time-3: raise ValueError('Carnet hors ordre')
            self.last_time = max(self.last_time, t)
        if kind == 'snapshot':
            bids, asks = {}, {}
            for key, out in [('bids', bids), ('asks', asks)]:
                for p, q, *_ in data[key]:
                    p,q = float(p),float(q)
                    if not math.isfinite(p+q) or p<=0 or q<0: raise ValueError('Niveau L2 invalide')
                    if q: out[p]=q
            self.bids, self.asks = bids, asks
            self.snapshot_ok = True
        elif kind == 'l2update':
            if not self.snapshot_ok: raise ValueError('Mise a jour sans snapshot')
            for side,p,q in data['changes']:
                p,q = float(p),float(q)
                if side not in ('buy','sell') or not math.isfinite(p+q) or p<=0 or q<0:
                    raise ValueError('Niveau L2 invalide')
                out = self.bids if side=='buy' else self.asks
                if q: out[p]=q  # absolute size, NOT a delta
                else: out.pop(p,None)
        self.last_recv=now
        if self.bids and self.asks and max(self.bids)>=min(self.asks):
            raise ValueError('Carnet croise : resynchronisation')

    def snapshot(self, exchange_time, now=None):
        now = time.monotonic() if now is None else now
        ready = (self.connected and self.snapshot_ok and now-self.last_recv<=3
                 and bool(self.bids) and bool(self.asks))
        base={'available': bool(ready), 'source':'Coinbase ETH-USD / level2_batch',
              'captured_at':float(exchange_time), 'exchange_time':self.last_time,
              'age_ms':round(max(0,now-self.last_recv)*1000) if self.last_recv else None,
              'reason':self.error if not ready else None, 'band_usd':1.0}
        if not ready: return base
        bid,ask=max(self.bids),min(self.asks);mid=(bid+ask)/2
        qb=sum(q for p,q in self.bids.items() if mid-1<=p<=mid)
        qa=sum(q for p,q in self.asks.items() if mid<=p<=mid+1)
        flow=[(q,s) for t,q,s in self.trades if exchange_time-5<t<=exchange_time]
        buy=sum(q for q,s in flow if s==1); sell=sum(q for q,s in flow if s==-1)
        flow_ok=self.flow_start is not None and exchange_time-self.flow_start>=5
        base.update(bid=bid,ask=ask,spread=ask-bid,bid_depth=qb,ask_depth=qa,
                    imbalance=(qb-qa)/(qb+qa) if qb+qa else 0.,
                    buy_volume_5s=buy,sell_volume_5s=sell,
                    flow_imbalance=(buy-sell)/(buy+sell) if buy+sell else 0.,
                    flow_available=flow_ok)
        return base


async def stream_book(on_connect, on_message, on_error, url=URL):
    backoff=1
    while True:
        try:
            async with connect(url,open_timeout=10,ping_interval=20,ping_timeout=10,
                               max_size=16*1024*1024,max_queue=64,close_timeout=2) as ws:
                await ws.send(json.dumps({'type':'subscribe','product_ids':[PRODUCT],
                                         'channels':['level2_batch','heartbeat']}))
                await on_connect()
                started=time.monotonic()
                while True:
                    raw=await asyncio.wait_for(ws.recv(),timeout=5)
                    d=json.loads(raw)
                    if d.get('type')=='heartbeat' and abs(time.time()-timestamp(d['time']))>5:
                        raise ValueError('Horloge ou flux L2 retarde de plus de 5 s')
                    await on_message(d)
                    if time.monotonic()-started>30: backoff=1
        except asyncio.CancelledError: raise
        except Exception as exc:
            await on_error(f'{type(exc).__name__}: {str(exc)[:150]}')
            await asyncio.sleep(backoff);backoff=min(30,backoff*2)
