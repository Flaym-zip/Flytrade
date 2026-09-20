"""Kraken spot ETH/USD, public read-only feed. No venue splice.
Settlement uses identified WS trades ONLY. Independent REST polling measures HTTP
availability; it never appends a Tick or manufactures a touch.
"""
from __future__ import annotations
import asyncio
import json
import math
import time
import zlib
from collections import deque
from contextlib import suppress
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
import httpx
from websockets.asyncio.client import connect
from .market import Market, timestamp
from .orderbook import OrderBook

KRAKEN_WS = 'wss://ws.kraken.com/v2'
KRAKEN_REST = 'https://api.kraken.com/0/public/Ticker?pair=ETHUSD'
SYMBOL = 'ETH/USD'
POLL_SECONDS = 2.0
HTTP_TIMEOUT = 1.6
PING_SECONDS = 1.0
HEALTH_LIMIT = 3.0
BOOK_DEPTH = 100


def positive(value, *, zero=False):
    x = float(value)
    if not math.isfinite(x) or x < 0 or (not zero and x == 0):
        raise ValueError('Valeur de marche invalide')
    return x


class HttpWatch:
    """Reception timestamps are NOT trade timestamps. Bounded diagnostics."""
    def __init__(self):
        self.attempts = self.successes = self.failures = self.missed_target = 0
        self.last_success = self.last_attempt = self.last_received_wall = None
        self.last_error = self.last_price = self.bid = self.ask = None
        self.latencies = deque(maxlen=300)
        self.intervals = deque(maxlen=300)
        self.cooldown_until = 0.

    def success(self, data, started, now=None):
        now = time.monotonic() if now is None else now
        if data.get('error'):
            raise ValueError('Kraken REST: ' + str(data['error'])[:100])
        result = data.get('result', {})
        item = result.get('XETHZUSD', result.get('ETHUSD', result.get('ETH/USD')))
        if not isinstance(item, dict): raise ValueError('Reponse REST sans ETH/USD')
        price, bid, ask = [positive(item[k][0]) for k in ('c', 'b', 'a')]
        if bid >= ask: raise ValueError('Cotations REST croisees')
        if self.last_success is not None:
            interval = now-self.last_success
            self.intervals.append(interval)
            self.missed_target += int(interval > HEALTH_LIMIT)
        self.last_success = now
        self.last_received_wall = time.time()
        self.last_price, self.bid, self.ask = price, bid, ask
        self.successes += 1
        self.last_error = None
        self.latencies.append(max(0., (now-started)*1000))

    def failure(self, exc):
        self.failures += 1
        self.last_error = f'{type(exc).__name__}: {str(exc)[:140]}'

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        age = None if self.last_success is None else max(0., now-self.last_success)
        latency = sorted(self.latencies)
        return dict(source='Kraken REST / ETH-USD / Ticker', poll_seconds=POLL_SECONDS,
                    timeout_seconds=HTTP_TIMEOUT, target_seconds=HEALTH_LIMIT,
                    response_age_ms=None if age is None else round(age*1000),
                    healthy=age is not None and age <= HEALTH_LIMIT,
                    successes=self.successes, failures=self.failures, attempts=self.attempts,
                    last_price=self.last_price, bid=self.bid, ask=self.ask,
                    received_at=self.last_received_wall, trade_timestamp=None,
                    latency_ms=self.latencies[-1] if self.latencies else None,
                    p95_latency_ms=latency[math.ceil(.95*len(latency))-1] if latency else None,
                    max_success_gap_s=max(self.intervals, default=None),
                    success_gaps_over_3s=self.missed_target,
                    cooldown_s=max(0., round(self.cooldown_until-now, 1)),
                    error=self.last_error, settlement_eligible=False)


async def poll_rest(watch: HttpWatch, *, url=KRAKEN_REST, client=None, stop_after=None):
    """Start every 2s normally, wall deadline 1.6s. Honor server throttling.
    No overlapping requests. Cancellation closes the pooled HTTP connection.
    """
    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False,
                                   headers={'User-Agent': 'Flytrade-alpha07-public-data'})
    count = 0
    try:
        while stop_after is None or count < stop_after:
            now = time.monotonic()
            if now < watch.cooldown_until: await asyncio.sleep(watch.cooldown_until-now)
            started = time.monotonic()
            watch.last_attempt = started
            watch.attempts += 1
            count += 1
            try:
                async with asyncio.timeout(HTTP_TIMEOUT):
                    response = await client.get(url)
                    if response.status_code in (429, 503):
                        try:
                            delay = float(response.headers.get('Retry-After', '10'))
                            if not math.isfinite(delay): delay = 10.
                        except ValueError:
                            try: delay = parsedate_to_datetime(response.headers['Retry-After']).timestamp()-time.time()
                            except (KeyError, ValueError, TypeError, OverflowError): delay = 10.
                        watch.cooldown_until = time.monotonic()+max(5., delay)
                    response.raise_for_status()
                    watch.success(response.json(), started)
            except asyncio.CancelledError: raise
            except Exception as exc:
                watch.failure(exc)
                if 'limit' in str(exc).lower() or 'throttl' in str(exc).lower():
                    watch.cooldown_until = max(watch.cooldown_until, time.monotonic()+10)
            if stop_after is None or count < stop_after:
                await asyncio.sleep(max(0., POLL_SECONDS-(time.monotonic()-started)))
    finally:
        if owned: await client.aclose()


class KrakenMarket(Market):
    source_id = 'kraken-ETH-USD'
    source_label = 'Kraken Spot / ETH-USD / trades identifies'
    ws_url = KRAKEN_WS

    def __init__(self, watch=None):
        super().__init__()
        self.watch = watch or HttpWatch()
        self.status = 'Connexion Kraken ETH/USD...'

    def receive(self, event, now=None):
        # Kraken pong provides exchange clock, NOT a last-trade-id heartbeat.
        if event['kind'] == 'clock': event = dict(event, id=self.last_id or 0)
        return super().receive(event, now)

    def snapshot(self, now=None):
        result = super().snapshot(now)
        result.update(source=self.source_label, url=self.ws_url, product='ETH-USD',
                      provider='kraken', source_id=self.source_id,
                      trade_age_ms=round(max(0., self.estimated_time(now)-self.last_trade_time)*1000)
                                   if self.last_trade_time else None,
                      rest=self.watch.snapshot(now), freshness_limit_seconds=HEALTH_LIMIT,
                      ping_seconds=PING_SECONDS,
                      settlement='Transactions WebSocket identifiees uniquement')
        return result


def check_status(data):
    if data.get('channel') == 'status':
        for row in data.get('data', []):
            if row.get('system') != 'online':
                raise ValueError('Kraken systeme non disponible : ' + str(row.get('system')))


def trade_events(data: dict[str, Any]):
    check_status(data)
    if data.get('success') is False:
        raise ValueError('Kraken: ' + str(data.get('error', 'abonnement refuse')))
    if data.get('method') in ('pong', 'subscribe') and data.get('time_out'):
        return [dict(kind='clock', time=timestamp(data['time_out']), id=0)]
    if data.get('channel') != 'trade': return []
    snapshot = data.get('type') == 'snapshot'
    rows = data.get('data')
    if not isinstance(rows, list): raise ValueError('Transactions Kraken invalides')
    events = []
    # Initial snapshot only seeds latest price; never invent 30s live observation.
    if snapshot:
        relevant = [r for r in rows if r.get('symbol') == SYMBOL]
        rows = [max(relevant, key=lambda r: int(r['trade_id']))] if relevant else []
    for row in rows:
        if row.get('symbol') != SYMBOL: continue
        tid = row['trade_id']
        if isinstance(tid, bool) or int(tid) != tid or tid < 0: raise ValueError('trade_id Kraken invalide')
        side = row.get('side')
        if side not in ('buy', 'sell'): raise ValueError('Cote taker inconnu')
        events.append(dict(kind='trade', time=timestamp(row['timestamp']),
                           price=positive(row['price']), id=int(tid), snapshot=snapshot,
                           size=positive(row['qty'], zero=True),
                           # Internal OrderBook.trade expects maker convention.
                           side='sell' if side == 'buy' else 'buy'))
    return events


async def _ping_loop(ws):
    request_id = 1000
    while True:
        await ws.send(json.dumps({'method': 'ping', 'req_id': request_id}))
        request_id += 1
        await asyncio.sleep(PING_SECONDS)


async def stream_kraken(on_connect, on_event, on_error, *, url=KRAKEN_WS):
    backoff = 1.
    while True:
        try:
            async with connect(url, open_timeout=8, ping_interval=20, ping_timeout=5,
                               close_timeout=1, max_size=2**20, max_queue=64) as ws:
                await on_connect()
                await ws.send(json.dumps({'method': 'subscribe', 'params': {
                    'channel': 'trade', 'symbol': [SYMBOL], 'snapshot': True}}))
                ping = asyncio.create_task(_ping_loop(ws)); started = time.monotonic()
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), HEALTH_LIMIT)
                        for event in trade_events(json.loads(raw)):
                            if event['kind'] == 'clock' and abs(time.time()-event['time']) > 5:
                                raise ValueError('Kraken : horloge ou transport decale > 5 s. Verifier NTP')
                            await on_event(event)
                        if time.monotonic()-started > 30: backoff = 1.
                finally:
                    ping.cancel()
                    with suppress(asyncio.CancelledError): await ping
        except asyncio.CancelledError: raise
        except Exception as exc:
            await on_error(f'Kraken WS {type(exc).__name__}: {str(exc)[:130]}')
            await asyncio.sleep(backoff); backoff = min(30., backoff*2)


def crc_book(bids, asks):
    def fmt(x): return format(x, 'f').replace('.', '').lstrip('0')
    text = ''.join(fmt(p)+fmt(q) for book, rev in ((asks, False), (bids, True))
                   for p, q in sorted(book.items(), reverse=rev)[:10])
    return zlib.crc32(text.encode()) & 0xffffffff


class KrakenBook(OrderBook):
    """Depth-limited L2, lossless decimals and CRC32 verification each update."""
    def __init__(self):
        super().__init__()
        self.dbids, self.dasks = {}, {}
        self.checksums = 0
        self.error = 'En attente du carnet Kraken'
        self.coverage = False

    def begin(self):
        super().begin()
        self.dbids.clear(); self.dasks.clear(); self.coverage = False

    def disconnect(self, reason):
        super().disconnect(reason)
        self.dbids.clear(); self.dasks.clear(); self.coverage = False

    def receive(self, data, now=None):
        now = time.monotonic() if now is None else now
        check_status(data)
        if data.get('success') is False: raise ValueError(str(data.get('error')))
        if not self.connected: raise ValueError('Carnet Kraken deconnecte')
        if self.last_recv and now-self.last_recv > HEALTH_LIMIT and self.snapshot_ok:
            raise ValueError('Carnet Kraken interrompu > 3 s')
        if data.get('method') in ('pong', 'subscribe'):
            if data.get('time_out'):
                t = timestamp(data['time_out'])
                if abs(time.time()-t) > 5: raise ValueError('Horloge Kraken carnet decalee')
                self.last_time = max(self.last_time, t)
            self.last_recv = now
            return
        if data.get('channel') == 'heartbeat':
            self.last_recv = now
            return
        if data.get('channel') != 'book': return
        for row in data.get('data', []):
            if row.get('symbol') != SYMBOL: continue
            snapshot = data.get('type') == 'snapshot'
            if not snapshot and not self.snapshot_ok: raise ValueError('L2 avant snapshot')
            bids = {} if snapshot else self.dbids.copy()
            asks = {} if snapshot else self.dasks.copy()
            for key, side in (('bids', bids), ('asks', asks)):
                for level in row.get(key, []):
                    p, q = Decimal(str(level['price'])), Decimal(str(level['qty']))
                    if not p.is_finite() or not q.is_finite() or p <= 0 or q < 0:
                        raise ValueError('Niveau Kraken non fini ou negatif')
                    if q: side[p] = q
                    else: side.pop(p, None)
            bids = dict(sorted(bids.items(), reverse=True)[:BOOK_DEPTH])
            asks = dict(sorted(asks.items())[:BOOK_DEPTH])
            if not bids or not asks or max(bids) >= min(asks): raise ValueError('Carnet Kraken vide ou croise')
            if crc_book(bids, asks) != int(row['checksum']):
                self.snapshot_ok = False
                raise ValueError('CRC32 Kraken incorrect : resynchronisation requise')
            self.checksums += 1
            self.dbids, self.dasks = bids, asks
            self.bids = {float(p): float(q) for p, q in bids.items()}
            self.asks = {float(p): float(q) for p, q in asks.items()}
            self.snapshot_ok = True
            self.last_recv = now
            if row.get('timestamp'):
                t = timestamp(row['timestamp'])
                if t < self.last_time-HEALTH_LIMIT: raise ValueError('Carnet Kraken trop ancien')
                self.last_time = max(self.last_time, t)
            mid = (max(self.bids)+min(self.asks))/2
            self.coverage = (len(bids) < BOOK_DEPTH or min(self.bids) <= mid-1) and (
                len(asks) < BOOK_DEPTH or max(self.asks) >= mid+1)

    def snapshot(self, exchange_time, now=None):
        out = super().snapshot(exchange_time, now)
        out.update(source='Kraken ETH-USD / book 100 / CRC32', checksum_checks=self.checksums,
                   depth_levels=BOOK_DEPTH, band_complete=self.coverage)
        if out['available'] and not self.coverage:
            out['available'] = False
            out['reason'] = '100 niveaux ne couvrent pas toute la bande +/-1 USD'
        return out


async def stream_kraken_book(on_connect, on_message, on_error, *, url=KRAKEN_WS):
    backoff = 1.
    while True:
        try:
            async with connect(url, open_timeout=8, close_timeout=1, ping_interval=20,
                               ping_timeout=5, max_size=4*1024*1024, max_queue=64) as ws:
                await on_connect()
                await ws.send(json.dumps({'method': 'subscribe', 'params': {
                    'channel': 'book', 'symbol': [SYMBOL], 'depth': BOOK_DEPTH, 'snapshot': True}}))
                ping = asyncio.create_task(_ping_loop(ws)); started = time.monotonic()
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), HEALTH_LIMIT)
                        await on_message(json.loads(raw, parse_float=Decimal))
                        if time.monotonic()-started > 30: backoff = 1.
                finally:
                    ping.cancel()
                    with suppress(asyncio.CancelledError): await ping
        except asyncio.CancelledError: raise
        except Exception as exc:
            await on_error(f'Kraken carnet {type(exc).__name__}: {str(exc)[:130]}')
            await asyncio.sleep(backoff); backoff = min(30., backoff*2)
