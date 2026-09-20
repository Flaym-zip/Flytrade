"""Read-only Coinbase trade feed. No wallets, order API or fabricated prices.

Exchange timestamps define the grid. Monotonic time only interpolates the camera
and measures transport freshness. A missed trade invalidates active experiments.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import connect

URL = "wss://ws-feed.exchange.coinbase.com"
PRODUCT = "ETH-USD"
SOURCE = "Coinbase Exchange / ETH-USD / matches"
STALE_SECONDS = 3.0
PRICE_MAX_AGE = 5.0
WARMUP_SECONDS = 30


def timestamp(value: str) -> float:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Horodatage invalide.")
    return result


def decode_message(raw: str | bytes) -> list[dict[str, Any]]:
    data = json.loads(raw)
    kind = data.get("type")
    if kind == "error":
        raise ValueError(str(data.get("message", "Erreur Coinbase")))
    if data.get("product_id") != PRODUCT:
        return []
    if kind in ("match", "last_match"):
        price = float(data["price"])
        if not math.isfinite(price) or not 0 < price < 1e9:
            raise ValueError("Prix invalide.")
        return [{"kind": "trade", "time": timestamp(data["time"]),
                 "price": price, "id": int(data["trade_id"]),
                 "snapshot": kind == "last_match", "size": data.get("size"), "side": data.get("side")}]
    if kind == "heartbeat":
        return [{"kind": "clock", "time": timestamp(data["time"]),
                 "id": int(data["last_trade_id"])}]
    return []


@dataclass(frozen=True)
class Tick:
    t: float
    price: float
    trade_id: int
    epoch: int


class Market:
    source_id = 'coinbase-ETH-USD'
    def __init__(self) -> None:
        self.ticks: deque[Tick] = deque(maxlen=120000)
        self.epoch = 0
        self.connected = False
        self.status = "Connexion au flux ETH-USD..."
        self.error: str | None = None
        self.last_id: int | None = None
        self.watermark = 0.0
        self.clock_recv = 0.0
        self.last_recv = 0.0
        self.last_trade_time = 0.0
        self.epoch_start: float | None = None
        self.reconnects = 0
        self.gaps = 0
        self.total_ticks = 0

    def begin(self, now: float | None = None) -> None:
        self.epoch += 1
        self.connected = True
        self.error = None
        self.status = "Collecte de 30 s d'historique continu"
        self.last_id = None
        self.epoch_start = None
        self.last_recv = time.monotonic() if now is None else now
        self.reconnects += 1

    def disconnect(self, reason: str) -> None:
        self.connected = False
        self.error = reason
        self.status = "Flux indisponible - apprentissage suspendu"
        self.epoch_start = None

    def estimated_time(self, now: float | None = None) -> float:
        if not self.watermark:
            return 0.0
        now = time.monotonic() if now is None else now
        # Freeze the view once transport is stale, never invent a moving market.
        return self.watermark + (min(STALE_SECONDS, max(0.0, now-self.clock_recv)) if self.connected else 0)

    def receive(self, event: dict[str, Any], now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else now
        t, tid = float(event["time"]), int(event["id"])
        if not self.connected:
            return None
        reason = None
        kind = event["kind"]
        if kind == "trade" and self.last_id is not None and tid <= self.last_id:
            return None  # Duplicate or already consumed message.
        if kind == "trade" and self.last_id is not None and tid != self.last_id + 1:
            reason = "Transaction manquante dans le flux (trade_id)."
        if kind == "clock" and self.last_id is not None and tid > self.last_id:
            reason = "Heartbeat : transactions non recues."
        if kind == "trade" and self.last_id is not None and t < self.last_trade_time:
            reason = "Transactions recues dans un ordre temporel incoherent."
        if self.last_recv and now-self.last_recv > STALE_SECONDS:
            reason = "Interruption du flux superieure a 3 s."
        if self.watermark and t < self.watermark - STALE_SECONDS and not event.get("snapshot"):
            reason = "Message de marche trop ancien."
        if reason:
            self.gaps += 1
            self.epoch += 1
            self.epoch_start = None
            self.last_id = None
            self.status = "Discontinuite : nouvelle collecte de 30 s"
        self.last_recv = now
        if t >= self.watermark:
            self.watermark = t
            self.clock_recv = now
        if kind == "trade":
            self.last_id = tid
            self.last_trade_time = t
            self.total_ticks += 1
            self.ticks.append(Tick(t, float(event["price"]), tid, self.epoch))
            if self.epoch_start is None:
                self.epoch_start = max(t, self.watermark)
        elif reason:
            # Resume from the heartbeat ID; the next trade must follow it.
            self.last_id = tid
        return reason

    def transport_healthy(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.connected and bool(self.watermark) and now-self.last_recv <= STALE_SECONDS

    def ready_reason(self, now: float | None = None) -> str | None:
        if not self.transport_healthy(now):
            return "Flux absent ou en retard. Aucun choix autorise."
        if not self.ticks or self.ticks[-1].epoch != self.epoch:
            return "En attente d'une transaction ETH-USD."
        if self.estimated_time(now)-self.last_trade_time > PRICE_MAX_AGE:
            return "Dernier prix trop ancien (> 5 s)."
        if self.epoch_start is None or self.watermark-self.epoch_start < WARMUP_SECONDS:
            return "Collecte initiale : 30 secondes continues sont necessaires."
        if self.history() is None:
            return "Historique continu insuffisant."
        return None

    def history(self) -> tuple[float, ...] | None:
        # A causal one-second sample. Never interpolate through future prices.
        end = math.floor(self.watermark)
        lower = end-WARMUP_SECONDS
        candidates = [x for x in self.ticks if x.epoch == self.epoch and x.t <= end]
        if not candidates or candidates[0].t > lower:
            return None
        times = [x.t for x in candidates]
        prices = []
        for sample_time in range(lower, end+1):
            i = bisect.bisect_right(times, sample_time)-1
            if i < 0:
                return None
            prices.append(candidates[i].price)
        return tuple(prices)

    def chart(self, now: float | None = None) -> list[list[float]]:
        cutoff = self.estimated_time(now)-150
        # Preserve first, min, max, last in each 200 ms bucket, in time order.
        buckets: dict[tuple[int, int], list[Tick]] = {}
        for tick in reversed(self.ticks):
            if tick.t < cutoff:
                break
            buckets.setdefault((tick.epoch, int(tick.t*5)), []).append(tick)
        out = []
        for values in reversed(list(buckets.values())):
            ordered = list(reversed(values))
            ix = sorted({0, len(ordered)-1,
                         min(range(len(ordered)), key=lambda i: ordered[i].price),
                         max(range(len(ordered)), key=lambda i: ordered[i].price)})
            out.extend([[ordered[i].t, ordered[i].price, ordered[i].epoch] for i in ix])
        return out

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        reason = self.ready_reason(now)
        warm = max(0.0, self.watermark-self.epoch_start) if self.epoch_start is not None else 0
        return {"source": SOURCE, "url": URL, "product": PRODUCT, "provider": "off" if getattr(self, "provider_off", False) else "coinbase",
                "source_id": self.source_id,
                "trade_age_ms": round(max(0,self.estimated_time(now)-self.last_trade_time)*1000) if self.last_trade_time else None,
                "connected": self.connected, "ready": reason is None,
                "reason": reason, "status": "Flux actif" if reason is None else self.status,
                "error": self.error, "exchange_time": self.estimated_time(now),
                "watermark": self.watermark,
                "price": self.ticks[-1].price if self.ticks else None,
                "age_ms": round(max(0, now-self.last_recv)*1000) if self.watermark else None,
                "warmup_seconds": min(WARMUP_SECONDS, round(warm, 1)),
                "gaps": self.gaps, "reconnects": self.reconnects,
                "ticks": self.total_ticks, "chart": self.chart(now)}


async def stream(on_connect: Callable[[], Awaitable[None]],
                 on_event: Callable[[dict[str, Any]], Awaitable[None]],
                 on_error: Callable[[str], Awaitable[None]], url: str = URL) -> None:
    """One upstream connection shared by every browser. Bounded reconnection."""
    backoff = 1.0
    while True:
        try:
            async with connect(url, open_timeout=10, ping_interval=20, ping_timeout=10,
                               max_size=2**20, max_queue=64, close_timeout=2) as ws:
                await ws.send(json.dumps({"type": "subscribe", "product_ids": [PRODUCT],
                                          "channels": ["matches", "heartbeat"]}))
                await on_connect()
                started = time.monotonic()
                while True:
                    message = await asyncio.wait_for(ws.recv(), timeout=5)
                    for event in decode_message(message):
                        if event["kind"] == "clock" and abs(time.time()-event["time"]) > 5:
                            raise ValueError("Horloge systeme decalee ou flux retarde de plus de 5 s ; verifier NTP.")
                        await on_event(event)
                    if time.monotonic()-started > 30:
                        backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning("ETH feed unavailable: %s", exc)
            await on_error(f"{type(exc).__name__}: {str(exc)[:160]}")
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff*2)
