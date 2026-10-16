# Market Lens — OpenBB backend

**Prepared and locally tested. Not deployed or connected to the Base44 UI yet.**

Personal research service for BTCUSD and XAUUSD. No order execution endpoints.
OpenBB v5 aggregates research sources; public Coinbase provides the Bitcoin quote and chart candles.

## What works

| Feature | Provider | Connection needed |
|---|---|---|
| BTC last trade, bid/ask, source time | Coinbase Exchange | No provider key |
| BTC closed daily candles, EMA50/200, Wilder RSI14 | Coinbase Exchange | No provider key |
| Daily nominal Treasury yields | OpenBB / Federal Reserve | No provider key |
| BTC research history | OpenBB / yfinance | No key; rate limits apply |
| Real yields, inflation, policy rates, broad dollar index | OpenBB / FRED | FRED API key |
| Gold quote | Optional OANDA adapter | Broker selection, account ID and API token |

Quote freshness uses the source timestamp, not retrieval time. Fresh means at most
60 seconds old. A daily macro observation is never labelled a live quote. Price refreshes
do not change the analyst's dated verdict. Market failures return explicit unavailable
states and null prices, without invented fallbacks.

## Run locally

Requires Python 3.12. From this directory:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/openbb-build
cp .env.example .env
```

Set a new random `MARKET_LENS_API_KEY` in your environment. `.env` is only a template;
the app does not automatically load it. With your environment populated:

```sh
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

`GET /health` is public. Every `/v1/` request requires `X-API-Key`.
Do not expose a local development server directly to the internet.

## Host on Render

The included `render.yaml` is a free-plan Docker Blueprint, not a deployed service.
Put this directory's contents at the root of a private Git repository, connect that
repository to Render, and create a Blueprint from it. Render generates the API key.
Do not approve a paid upgrade without reviewing its cost. Free hosting may sleep or
have insufficient resources; actual Docker build, memory and cold starts need verification.
No cloud service or charge was created in preparing this package.

Once deployed, check `/health`, then call authenticated `/v1/status` and each required
data endpoint. A successful health check does not verify the upstream providers.
Keep the Render-generated key in secret settings. Never paste it into chat or commit it.

## Connect Base44

Use the Base44 app associated with your Market Lens dashboard.

1. Add the included `base44/functions/market-data/entry.ts` through the Base44 builder
   or an authorised sandbox. Backend functions must be supported on the app's plan.
2. In Base44 server-side secrets, set `MARKET_LENS_BACKEND_URL` to the deployed HTTPS
   origin and `MARKET_LENS_API_KEY` to the same secret used by the Python server.
3. Call the function from the UI; the SDK returns the JSON under `.data`:

```js
const response = await base44.functions.invoke('market-data', {operation: 'btc_quote'});
const quote = response.data;
```

The proxy authenticates the app user and permits fixed operations only. It is not
an arbitrary URL proxy. Do not put backend or broker keys in browser code/localStorage.
The example `base44/MarketDataPanel.jsx` shows a quote panel and safe polling lifecycle;
it is reference code, not an installed dashboard component.

| Operation | Backend endpoint |
|---|---|
| status | `/v1/status` |
| btc_quote | `/v1/market/BTCUSD` |
| gold_quote | `/v1/market/XAUUSD` |
| candles | `/v1/candles/BTCUSD` |
| history | `/v1/history/BTCUSD` |
| treasury | `/v1/treasury` |
| macro + series | `/v1/macro/{series}` |

Macro allowlist: `DGS10`, `DFII10`, `CPIAUCSL`, `DFF`, `DTWEXBGS`. Set `FRED_API_KEY`
on the Python server to enable it. Treasury output is percentage points (e.g. 5.19
means 5.19%), converted explicitly from OpenBB decimal fractions. Treasury rates are
nominal yields; FRED `DFII10` supplies real yields. They are distinct series.

Gold remains unconfigured by default. For OANDA, set server-side `OANDA_API_TOKEN`,
`OANDA_ACCOUNT_ID` and `OANDA_ENVIRONMENT=practice` or `live`. Confirm XAU_USD is
available for that account. This code only requests pricing; broker token permissions
are governed by OANDA and may be broader than pricing. No trading endpoints are included.

## Validation and limits

Install `pytest==9.1.1` separately, then run `python -m pytest -q`.
See `VERIFICATION.md` for observed checks and remaining deployment work.

No automated news, ETF, on-chain research, PDF ingestion, push alerts, background
monitoring or trading is implemented here. Analyst conclusions require their own
dated review. Provider access terms and availability still apply to open-source tools.

Official references:
- https://docs.openbb.co/odp/python/quickstart/user
- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker
- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- https://developer.oanda.com/rest-live-v20/pricing-ep/
- https://render.com/docs/blueprint-spec
