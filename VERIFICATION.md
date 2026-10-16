# Verification — 16 October 2026

## Local runtime

Python 3.12.14. Installed and imported OpenBB core 2.0.1 and provider extensions
yfinance, FRED and Federal Reserve 2.0.0. Verified OpenBB v5 provider namespaces
against the installed interfaces. Requirements pin the direct dependencies.

Automated checks: **11 passed** (`pytest -q`, 0.33 seconds). Covers authentication,
missing server keys, source freshness, completed candles, missing candle gaps,
indicator warm-up, failure output, unconfigured feeds, symbol allowlists, Treasury
unit conversion, and overlapping provider workers after a timeout.

## Real provider checks

- Coinbase BTC quote succeeded. Source last-trade time `2026-10-16T03:16:43.236514388Z`;
  retrieved `2026-10-16T03:16:44.395238Z`; age 1.16 seconds. Price at that check:
  $82,083.14. This is a recorded test result, not a current quote when reading this file.
- Coinbase daily candles succeeded: 349 complete candles, most recent 15 October 2026 UTC.
  EMA50 80,330.084; EMA200 75,579.8893; Wilder RSI14 48.67. These use Coinbase history
  and can differ from another venue or a longer EMA warm-up history.
- OpenBB Federal Reserve Treasury call succeeded: 20 rows over the requested 30-day
  window, latest observation 14 October 2026. Provider returns decimal fractions;
  response conversion to percent is covered by a regression test.
- OpenBB yfinance BTC history encountered provider HTTP429/rate limiting. The backend
  returned `unavailable`, with an empty history instead of synthetic or substituted data.
- Gold correctly returned `not_configured` with a null price, because no broker credentials
  were supplied. FRED macro correctly returned `not_configured` with no key.

## Remaining checks

Cloud deployment, Docker build, hosted resource limits/cold starts, Base44 function
deployment, frontend bundling/rendering and end-to-end app calls have not been verified.
The included JSX and Deno proxy are prepared references; they are not installed app code.
No Render account connection or hosted service URL was available at preparation time.
Base44 sandbox access reported PREMIUM_REQUIRED, so the built-in builder was asked
to apply the user's OpenBB request. Its final screen has not been verified.

No provider API keys, backend secrets or account IDs are included in this package.
