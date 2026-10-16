from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import app as backend

UTC = timezone.utc


def test_auth_is_required(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_API_KEY", "test-key")
    client = TestClient(backend.app)
    assert client.get("/health").status_code == 200
    assert client.get("/v1/status").status_code == 401
    assert client.get("/v1/status", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/status", headers={"X-API-Key": "test-key"}).status_code == 200


def test_missing_server_key_fails_closed(monkeypatch):
    monkeypatch.delenv("MARKET_LENS_API_KEY", raising=False)
    assert TestClient(backend.app).get("/v1/status").status_code == 503


def test_fresh_stale_invalid_and_future_timestamps():
    at = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)
    assert backend.freshness(backend.iso(at - timedelta(seconds=20)), at)["state"] == "fresh"
    assert backend.freshness(backend.iso(at - timedelta(seconds=61)), at)["state"] == "stale"
    assert backend.freshness("not-a-date", at)["state"] == "unknown"
    assert backend.freshness(backend.iso(at + timedelta(seconds=30)), at)["state"] == "clock_skew"


def test_closed_candles_only():
    at = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)
    start = int(datetime(2026, 10, 15, tzinfo=UTC).timestamp())
    bars = backend.normalise_coinbase_candles([[start + 86400, 98, 102, 99, 100, 5], [start, 98, 102, 99, 100, 5]], at)
    assert len(bars) == 1
    assert bars[0]["time"].startswith("2026-10-15")


def test_indicators_and_insufficient_history():
    bars = [{"close": 100, "time": str(i)} for i in range(250)]
    result = backend.technicals(bars)
    assert result["ema_50"] == result["ema_200"] == 100
    assert result["rsi_14"] == 50
    assert backend.technicals(bars[:30])["ema_200"] is None


def test_upstream_failure_never_becomes_fake_price(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_API_KEY", "test-key")
    backend._cache.clear()
    backend._locks.clear()
    async def fail(*args, **kwargs):
        raise ValueError("secret upstream details")
    monkeypatch.setattr(backend, "json_get", fail)
    data = TestClient(backend.app).get("/v1/market/BTCUSD", headers={"X-API-Key": "test-key"}).json()
    assert data["status"] == "unavailable"
    assert data["price"] is None
    assert "secret" not in str(data)


def test_gold_missing_credentials_is_explicit(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_API_KEY", "test-key")
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    data = TestClient(backend.app).get("/v1/market/XAUUSD", headers={"X-API-Key": "test-key"}).json()
    assert data["status"] == "not_configured" and data["price"] is None


def test_macro_missing_key_and_symbol_allowlist(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_API_KEY", "test-key")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    client = TestClient(backend.app)
    headers = {"X-API-Key": "test-key"}
    assert client.get("/v1/macro/DFII10", headers=headers).json()["status"] == "not_configured"
    assert client.get("/v1/macro/UNSUPPORTED", headers=headers).status_code == 400
    assert client.get("/v1/market/OTHER", headers=headers).status_code == 400


def test_openbb_treasury_decimal_units_become_percent(monkeypatch):
    import sys
    from types import SimpleNamespace
    class Row:
        def model_dump(self, **kwargs):
            return {"date": "2026-10-14", "year_10": 0.0519, "month_2": None}
    fake = SimpleNamespace(obb=SimpleNamespace(federal_reserve=SimpleNamespace(
        treasury_rates=lambda **kwargs: SimpleNamespace(results=[Row()]))))
    monkeypatch.setitem(sys.modules, "openbb", fake)
    result = backend.treasury_sync(30)
    assert result["unit"] == "percent"
    assert result["rows"][0]["year_10"] == 5.19
    assert result["rows"][0]["month_2"] is None


def test_missing_daily_candle_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="Missing daily candles"):
        backend.normalise_coinbase_candles([[0, 98, 102, 99, 100, 5], [172800, 98, 102, 99, 100, 5]])


def test_provider_worker_cannot_overlap_after_timeout():
    import pytest
    backend._provider_thread_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="busy"):
            backend.provider_call(lambda: None)
    finally:
        backend._provider_thread_lock.release()
