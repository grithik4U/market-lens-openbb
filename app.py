"""Market Lens: read-only market data, OpenBB research, explicit provenance.

No trade execution endpoints. Provider credentials stay on the server.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import math
import os
import secrets
import time
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

UTC = timezone.utc
app = FastAPI(title="Market Lens OpenBB Backend", version="1.0.0")
_cache: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_provider_lock = asyncio.Lock()
_provider_thread_lock = threading.Lock()


def provider_call(function, *args):
    # A timed-out async request cannot cancel an upstream worker thread.
    # Keep one actual OpenBB call active, even after the request timeout.
    if not _provider_thread_lock.acquire(blocking=False):
        raise RuntimeError("OpenBB worker is busy")
    try:
        return function(*args)
    finally:
        _provider_thread_lock.release()


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Source timestamp has no timezone")
    return result.astimezone(UTC)


def freshness(observed: str | None, at: datetime | None = None) -> dict:
    try:
        age = ((at or now()) - parse_timestamp(observed or "")).total_seconds()
    except (ValueError, TypeError):
        return {"state": "unknown", "age_seconds": None}
    if age < -5:
        return {"state": "clock_skew", "age_seconds": round(age, 2)}
    return {"state": "fresh" if age <= 60 else "stale", "age_seconds": round(max(0, age), 2)}


async def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("MARKET_LENS_API_KEY", "")
    if not expected:
        raise HTTPException(503, "Backend API key is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Invalid backend API key")


async def cached(key: str, ttl: int, fetch):
    async with _locks.setdefault(key, asyncio.Lock()):
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < ttl:
            return item[1]
        result = await fetch()
        _cache[key] = (time.monotonic(), result)
        return result


async def json_get(url: str, *, params=None, headers=None) -> tuple[Any, str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), follow_redirects=False) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json(), iso(now())


def positive(value: Any) -> float:
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise ValueError("Invalid market price")
    return n


def technicals(bars: list[dict]) -> dict:
    closes = [positive(b["close"]) for b in bars]

    def ema(period: int):
        if len(closes) < period:
            return None
        value = sum(closes[:period]) / period
        alpha = 2 / (period + 1)
        for close in closes[period:]:
            value = alpha * close + (1 - alpha) * value
        return round(value, 4)

    rsi = None
    if len(closes) >= 15:
        changes = [b - a for a, b in zip(closes, closes[1:])]
        gain = sum(max(x, 0) for x in changes[:14]) / 14
        loss = sum(max(-x, 0) for x in changes[:14]) / 14
        for change in changes[14:]:
            gain = (13 * gain + max(change, 0)) / 14
            loss = (13 * loss + max(-change, 0)) / 14
        rsi = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return {
        "ema_50": ema(50), "ema_200": ema(200),
        "rsi_14": round(rsi, 2) if rsi is not None else None,
        "closed_bars": len(bars), "as_of": bars[-1]["time"] if bars else None,
        "timeframe": "1D", "method": "EMA seeded with period SMA; Wilder RSI; closed candles only",
    }


async def btc_quote() -> dict:
    async def fetch():
        raw, retrieved = await json_get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
        observed = raw.get("time")
        parse_timestamp(observed)
        bid, ask, price = positive(raw["bid"]), positive(raw["ask"]), positive(raw["price"])
        if bid > ask:
            raise ValueError("Crossed bid/ask")
        return {
            "symbol": "BTCUSD", "instrument": "BTC-USD", "provider": "Coinbase Exchange",
            "price": price, "price_type": "last_trade", "bid": bid, "ask": ask,
            "source_observed_at": observed, "timestamp_kind": "last_trade",
            "retrieved_at": retrieved,
            "limitations": "Bid/ask timestamps are not supplied separately. Volume is Coinbase venue volume, not global BTC volume.",
        }
    result = dict(await cached("btc_quote", 5, fetch))
    result["freshness"] = freshness(result["source_observed_at"])
    result["status"] = result["freshness"]["state"]
    result["served_at"] = iso(now())
    return result


def normalise_coinbase_candles(rows: list, at: datetime | None = None) -> list[dict]:
    cutoff = (at or now()).timestamp()
    dedup = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("Invalid Coinbase candle")
        stamp, low, high, opening, close, volume = row[:6]
        stamp = int(stamp)
        if stamp + 86400 > cutoff:
            continue
        low, high, opening, close = map(positive, [low, high, opening, close])
        volume = float(volume)
        if not math.isfinite(volume) or volume < 0 or low > min(opening, close) or high < max(opening, close) or high < low:
            raise ValueError("Invalid OHLCV data")
        dedup[stamp] = {"time": iso(datetime.fromtimestamp(stamp, UTC)), "open": opening,
                        "high": high, "low": low, "close": close, "volume": volume}
    stamps = sorted(dedup)
    if any(b - a != 86400 for a, b in zip(stamps, stamps[1:])):
        raise ValueError("Missing daily candles; indicators withheld")
    return [dedup[t] for t in stamps]


async def btc_candles() -> dict:
    async def fetch():
        raw, retrieved = await json_get("https://api.exchange.coinbase.com/products/BTC-USD/candles", params={"granularity": 86400})
        if not isinstance(raw, list):
            raise ValueError("Invalid candles response")
        bars = normalise_coinbase_candles(raw)
        if not bars:
            raise ValueError("No completed daily candles")
        return {"status": "ok", "symbol": "BTCUSD", "provider": "Coinbase Exchange", "interval": "1D",
                "volume_type": "Coinbase exchange BTC volume", "retrieved_at": retrieved,
                "data_as_of": bars[-1]["time"], "bars": bars, "indicators": technicals(bars)}
    result = dict(await cached("btc_candles", 300, fetch))
    last = parse_timestamp(result["data_as_of"])
    result["history_current"] = last.date() == now().date() - timedelta(days=1)
    if not result["history_current"]:
        result["status"] = "stale"
    return result


async def gold_quote() -> dict:
    token = os.environ.get("OANDA_API_TOKEN", "")
    account = os.environ.get("OANDA_ACCOUNT_ID", "")
    if not token or not account:
        return {"symbol": "XAUUSD", "status": "not_configured", "price": None,
                "provider": None, "source_observed_at": None,
                "message": "Select your gold broker/provider. Optional OANDA pricing adapter is prepared but not connected."}
    environment = os.environ.get("OANDA_ENVIRONMENT", "practice")
    if environment not in {"practice", "live"}:
        raise ValueError("OANDA_ENVIRONMENT must be practice or live")
    host = "https://api-fxpractice.oanda.com" if environment == "practice" else "https://api-fxtrade.oanda.com"

    async def fetch():
        data, retrieved = await json_get(f"{host}/v3/accounts/{account}/pricing", params={"instruments": "XAU_USD"}, headers={"Authorization": f"Bearer {token}"})
        rows = [p for p in data.get("prices", []) if p.get("instrument") == "XAU_USD"]
        if not rows:
            raise ValueError("No XAU_USD price returned")
        row = rows[0]
        bid, ask = positive(row["bids"][0]["price"]), positive(row["asks"][0]["price"])
        if bid > ask:
            raise ValueError("Crossed gold bid/ask")
        parse_timestamp(row["time"])
        return {"symbol": "XAUUSD", "instrument": "XAU_USD", "provider": f"OANDA {environment}",
                "price": (bid + ask) / 2, "price_type": "bid_ask_midpoint", "bid": bid, "ask": ask,
                "source_observed_at": row["time"], "timestamp_kind": "broker_quote", "retrieved_at": retrieved,
                "market_status": row.get("status", "unknown")}
    result = dict(await cached("gold_quote", 5, fetch))
    result["freshness"] = freshness(result["source_observed_at"])
    result["status"] = result["freshness"]["state"] if result["market_status"] == "tradeable" else "market_closed"
    result["served_at"] = iso(now())
    return result


def openbb_history_sync(symbol: str, days: int) -> dict:
    # Verified against installed OpenBB v5: each provider has its own namespace.
    from openbb import obb
    start = date.today() - timedelta(days=days)
    result = obb.yfinance.crypto.price.historical(symbol="BTC-USD", start_date=start, end_date=date.today(), interval="1d")
    rows = [r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in (result.results or [])]
    if not rows:
        raise ValueError("OpenBB provider returned no historical records")
    return {"status": "ok", "symbol": symbol, "engine": "OpenBB", "provider": "yfinance",
            "retrieved_at": iso(now()), "data_as_of": max(str(r.get("date", "")) for r in rows),
            "rows": rows, "limitations": "Historical daily research data; not an executable/live broker price. Provider licence and availability apply."}


def macro_sync(series: str, days: int) -> dict:
    from openbb import obb
    result = obb.fred.economy.fred_series(symbol=series, start_date=date.today() - timedelta(days=days))
    rows = [r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in (result.results or [])]
    if not rows:
        raise ValueError("FRED returned no records")
    return {"status": "ok", "series": series, "engine": "OpenBB", "provider": "FRED",
            "retrieved_at": iso(now()), "rows": rows, "metadata": result.extra or {},
            "limitations": "Observation dates are not publication timestamps. Daily/monthly series may be revised; this is not a live tick."}


def treasury_sync(days: int) -> dict:
    from openbb import obb
    result = obb.federal_reserve.treasury_rates(start_date=now().date() - timedelta(days=days))
    rows = [r.model_dump(mode="json") for r in (result.results or [])]
    if not rows:
        raise ValueError("Federal Reserve returned no Treasury records")
    # OpenBB v5 federal_reserve normalizes percentage yields to decimal fractions.
    rows = [{key: (round(float(value) * 100, 6) if key != "date" and value is not None else value)
             for key, value in row.items()} for row in rows]
    rows.sort(key=lambda row: str(row["date"]))
    return {"status": "ok", "engine": "OpenBB", "provider": "Federal Reserve",
            "series": "nominal_treasury_rates", "unit": "percent", "rows": rows,
            "retrieved_at": iso(now()), "data_as_of": max(str(r["date"]) for r in rows),
            "limitations": "Daily published nominal Treasury yields; not live ticks or real yields. Publication lag applies."}


def safe_error(exc: Exception) -> dict:
    # Do not return upstream URLs, account ids, headers, tokens or tracebacks.
    if isinstance(exc, httpx.HTTPStatusError):
        return {"status": "unavailable", "error": "upstream_http_error", "upstream_status": exc.response.status_code}
    if isinstance(exc, (httpx.RequestError, TimeoutError)):
        return {"status": "unavailable", "error": "upstream_connection_error"}
    return {"status": "unavailable", "error": "provider_data_error"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "market-lens-openbb", "version": "1.0.0"}


@app.get("/v1/status", dependencies=[Depends(require_key)])
async def status():
    versions = {}
    for package in ["openbb-core", "openbb-yfinance", "openbb-fred", "openbb-federal-reserve"]:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"service": "market-lens-openbb", "openbb_packages": versions,
            "connections": {"btc_quotes": "public_coinbase_available_adapter",
                            "btc_history": "openbb_yfinance_available_adapter",
                            "treasury": "openbb_federal_reserve_available_adapter",
                            "gold": "configured_not_verified" if os.environ.get("OANDA_API_TOKEN") and os.environ.get("OANDA_ACCOUNT_ID") else "not_configured",
                            "macro": "configured_not_verified" if os.environ.get("FRED_API_KEY") else "not_configured"},
            "served_at": iso(now()), "note": "Configured does not mean recently verified. Fetch each endpoint to test it."}


@app.get("/v1/market/{symbol}", dependencies=[Depends(require_key)])
async def market(symbol: str):
    if symbol not in {"BTCUSD", "XAUUSD"}:
        raise HTTPException(400, "Supported symbols: BTCUSD, XAUUSD")
    try:
        return await (btc_quote() if symbol == "BTCUSD" else gold_quote())
    except Exception as exc:
        return {"symbol": symbol, **safe_error(exc), "price": None, "served_at": iso(now())}


@app.get("/v1/candles/BTCUSD", dependencies=[Depends(require_key)])
async def candles():
    try:
        return await btc_candles()
    except Exception as exc:
        return {"symbol": "BTCUSD", **safe_error(exc), "bars": [], "indicators": None}


@app.get("/v1/history/BTCUSD", dependencies=[Depends(require_key)])
async def history(days: int = Query(365, ge=30, le=730)):
    async def fetch():
        async with _provider_lock:
            return await asyncio.wait_for(asyncio.to_thread(provider_call, openbb_history_sync, "BTCUSD", days), timeout=45)
    try:
        return await cached(f"openbb_history_{days}", 900, fetch)
    except Exception as exc:
        return {"symbol": "BTCUSD", "engine": "OpenBB", **safe_error(exc), "rows": []}


@app.get("/v1/macro/{series}", dependencies=[Depends(require_key)])
async def macro(series: str, days: int = Query(365, ge=30, le=730)):
    if series not in {"DGS10", "DFII10", "CPIAUCSL", "DFF", "DTWEXBGS"}:
        raise HTTPException(400, "Unsupported macro series")
    if not os.environ.get("FRED_API_KEY"):
        return {"status": "not_configured", "provider": "FRED", "series": series, "rows": [],
                "message": "Set FRED_API_KEY in the backend environment to enable OpenBB macro data."}
    async def fetch():
        async with _provider_lock:
            return await asyncio.wait_for(asyncio.to_thread(provider_call, macro_sync, series, days), timeout=45)
    try:
        return await cached(f"macro_{series}_{days}", 3600, fetch)
    except Exception as exc:
        return {"series": series, "engine": "OpenBB", **safe_error(exc), "rows": []}


@app.get("/v1/treasury", dependencies=[Depends(require_key)])
async def treasury(days: int = Query(365, ge=30, le=730)):
    async def fetch():
        async with _provider_lock:
            return await asyncio.wait_for(asyncio.to_thread(provider_call, treasury_sync, days), timeout=45)
    try:
        return await cached(f"treasury_{days}", 3600, fetch)
    except Exception as exc:
        return {"engine": "OpenBB", "provider": "Federal Reserve", **safe_error(exc), "rows": []}
