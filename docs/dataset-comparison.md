# Data Sources Comparison

fin3 is provider-agnostic: the same `get_data()` call works against any
registered provider, and you choose a source per library based on asset type,
cost tolerance, and data-quality needs. This page compares the **providers**
fin3 supports, then dives into the **Databento dataset variants** (the
deepest, most nuanced source).

Audited 2025-05-26 (Databento figures); updated 2026-06-30 to cover Massive.

## Provider Comparison

| Provider | Asset scope | Cost | Auth | Intraday history | `max_cost` ceiling | Price basis (fin3 default) |
|---|---|---|---|---|---|---|
| **Databento** | US equities, futures | Paid, usage-based (pay-per-request) | API key | Years (per dataset) | ✅ Enforced (real per-query cost) | Raw |
| **Massive** (was Polygon.io) | US equities headline (options/forex/futures/crypto also available) | Paid subscription, limited free tier | API key | Tier-dependent | ❌ Not enforced (subscription) | Raw |
| **Yahoo Finance** | US equities/ETFs (also FX, indices, futures, crypto via Yahoo) | Free | None (keyless scraper) | Limited (`1m`→7d … `60m`→730d); daily unlimited | N/A (free) | Raw |
| **Binance** | Crypto spot | Free | Optional key (higher weight) | Deep | N/A (free) | Raw |

All providers normalize to the canonical OHLCV schema with a UTC
`DatetimeIndex`. **Raw-default** across the board (split/dividend-unadjusted)
for cross-provider parity; flip via each provider's config for adjusted prices.

### How to choose

- **Prototyping / research on US equities without a key** → **Yahoo** (free,
  keyless; unofficial scraper, so not a sole production source).
- **Crypto, any resolution, free** → **Binance** (free public klines, 24/7).
- **Production US-equity OHLCV, consolidated across all venues** → **Massive**
  (consolidated: 19 NMS exchanges + dark pools + FINRA + OTC; subscription).
- **Institutional equities/futures, depth (MBO/MBP), precise per-query cost
  control** → **Databento** (usage-based; the only provider where
  `max_cost` is a real budget guardrail).

### Provider notes

#### Databento

- **Cost model**: usage-based, pay-per-request. The only provider that exposes
  real per-query cost, so `MarketDataFetcher(max_cost=...)` is an actual budget
  ceiling (`CostLimitExceededError` on overrun).
- **Strength**: deep history, full depth-of-book (MBO/MBP-1/MBP-10), tick data.
- **Caveat**: dataset choice matters — single-venue datasets (e.g. `XNAS.ITCH`)
  produce null bars for symbols that rarely trade on that venue. See the
  Databento Datasets section below.
- **Price basis**: raw by default; Databento MBO/P provides native adjusted
  prices via separate schemas.

#### Massive (formerly Polygon.io)

Polygon.io rebranded to **Massive** (massive.com) on 2025-10-30; APIs, keys,
and data are unchanged, and `api.massive.com` is the rebrand host
(`api.polygon.io` runs in parallel). fin3 targets `api.massive.com` by default
(configurable via `MassiveConfig.base_url`).

- **Cost model**: subscription tiers (limited free tier). Subscription-based, so
  `estimate_cost()` returns `0.0` and the `max_cost` ceiling is **not
  enforced** — budget control is a plan-level concern, not per-query.
- **Strength**: consolidated US-equity bars across all NMS exchanges, dark
  pools, FINRA, and OTC. This avoids the single-venue null-bar problem some
  Databento datasets have.
- **Coverage**: US equities is the v1 scope; options, forex, crypto, and
  futures are also available from Massive but out of fin3's v1 scope (crypto is
  served by Binance).
- **Resolution**: native arbitrary multiplier — `4h` maps to `4×hour` directly
  (no aggregation fallback, unlike Yahoo).
- **Price basis**: `MassiveConfig(adjusted=False)` by default (the API default
  is adjusted; fin3 sends `adjusted=false` explicitly for Databento parity).

#### Yahoo Finance

- **Cost model**: free. yfinance is an unofficial scraper — can break or get
  rate-limited without notice. Treat as a prototyping/research source, not a
  sole production source.
- **Strength**: keyless, zero setup, great for a quick look or backtest sketch.
- **Caveat**: intraday history is bounded (`1m`→7 days, `5m`–`30m`→60 days,
  `60m`→730 days); daily is unrestricted. No native `4h` — `4h` requests fetch
  `1h` bars and aggregate up via `core._aggregate_bars`.
- **Price basis**: `YahooConfig(auto_adjust=False)` by default.

#### Binance

- **Cost model**: free. Public spot klines endpoint, no auth required
  (supplying a key only raises your per-IP weight allowance).
- **Strength**: the natural crypto source — 24/7 markets, deep history, clean
  klines.
- **Symbol mapping**: fin3's `BASE-USD` convention (e.g. `BTC-USD`) maps to
  Binance's `USDT` quote (`BTCUSDT`) automatically.
- **Caveat**: if `api.binance.com` is geo-blocked, point `BinanceConfig` at a
  public mirror like `https://data-api.binance.vision`.

---

## Databento Datasets (US Equities deep-dive)

Within Databento, the **dataset** choice (not just the provider) drives
coverage, history, and cost. Costs below are usage-based (pay-per-request),
not subscription.

### Cost Comparison

#### 8 critical symbols (SLV, SMCI, MSTR, META, AAPL, MARA, RIOT, XLRE)

| Dataset | Schema | Date Range | Cost |
|---|---|---|---|
| ARCX.PILLAR | ohlcv-1m | 2018-05 to 2025-11 | $4.36 |
| XNAS.ITCH | ohlcv-1m | 2020-01 to 2025-11 | $3.83 |
| XNAS.BASIC | ohlcv-1m | 2024-07 to 2025-11 | $1.37 |
| EQUS.SUMMARY | ohlcv-1d | 2024-07 to 2025-11 | $0.0044 |

#### Full 60 symbols

| Dataset | Schema | Date Range | Cost |
|---|---|---|---|
| ARCX.PILLAR | ohlcv-1m | 2018-05 to 2025-11 | $34.87 |
| XNAS.ITCH | ohlcv-1m | 2020-01 to 2025-11 | $27.32 |
| XNAS.BASIC | ohlcv-1m | 2024-07 to 2025-11 | $8.02 |
| EQUS.SUMMARY | ohlcv-1d | 2024-07 to 2025-11 | $0.0324 |

### Dataset Details

#### XNAS.ITCH (Nasdaq TotalView) -- current default

- **Coverage**: Nasdaq trades only. No off-exchange (TRF) trades.
- **History**: From 2020-01 (OHLCV aggregates), full depth from 2007.
- **Problem**: Only captures trades executed on Nasdaq. Symbols primarily traded
  on NYSE Arca (SLV, XLRE, etc.) have 10-20% null bars because many minutes
  have zero Nasdaq prints despite high consolidated volume.
- **Schemas**: ohlcv-1m, ohlcv-1d, ohlcv-1h, ohlcv-1s, MBO, MBP-1, MBP-10, trades, etc.

#### ARCX.PILLAR (NYSE Arca Integrated) -- recommended for 1m

- **Coverage**: NYSE Arca full depth of book. NYSE Arca is the leading exchange
  for ETF listing and trading (~10% of all US equities ADV). 71.1% of time at NBBO
  for all US ETFs.
- **History**: From 2018-05.
- **Advantage**: Captures Arca trades for ETFs (SLV, XLRE, XLU, etc.) where XNAS.ITCH
  has sparse data. Also captures many large-cap names that trade on Arca.
- **Schemas**: ohlcv-1m, ohlcv-1d, ohlcv-1h, ohlcv-1s, MBO, MBP-1, MBP-10, trades, imbalance, etc.
- **Symbol convention**: CMS (e.g. `BRK B`), not Nasdaq convention (`BRK.B`).

#### XNAS.BASIC (Nasdaq Basic with NLS Plus)

- **Coverage**: Includes off-exchange trades reported to FINRA/Nasdaq TRFs.
  Captures majority of non-lit market volume.
- **History**: From 2024-07 only. Not suitable for historical backfill before that.
- **Schemas**: ohlcv-1m, ohlcv-1d, ohlcv-1h, ohlcv-1s, trades, BBO-1s, BBO-1m, etc.
- **Note**: Would be a good supplement to ARCX.PILLAR for recent data, but limited history.

#### EQUS.SUMMARY (Databento US Equities Summary)

- **Coverage**: Consolidated across all 15 NMS exchanges + 30 ATSs. 100% intraday
  volume on a delayed basis, consolidated end-of-day prices.
- **History**: From 2024-07 only.
- **Schemas**: ohlcv-1d, definition, statistics. **No ohlcv-1m.** Not viable for 1m data.
- **Cost**: Extremely cheap for daily data.
- **Note**: Ideal for daily bars, useless for intraday.

### Subscription Pricing (for reference)

The Standard plan ($199/month) includes unlimited access to:
- 7 years of OHLCV history across all equities datasets
- 12 months of L0/L1 history
- 1 month of L2 (MBP-10) and L3 (MBO) history
- Live data with no exchange license fees

All datasets above are part of the Databento US Equities service.

### Null Bar Root Cause

The 17% null rate on SLV (and similar rates on XLRE, SMCI, MSTR, META) in
`equities-1m-databento` is caused by using `XNAS.ITCH`, which only reports bars
when trades execute on Nasdaq. SLV trades ~35M shares/day consolidated but only
a fraction on Nasdaq. Individual minutes with zero Nasdaq prints produce null
OHLCV bars.

Re-downloading from `XNAS.ITCH` does not help (verified by deleting and
re-downloading SLV -- identical 17% null rate). For symbols that trade
primarily off-Nasdaq, either switch dataset (ARCX.PILLAR for Arca-listed ETFs)
or switch provider (Massive consolidates across all venues).

### Cost API

`client.metadata.get_cost()` returns a `float` (the billable cost in USD), not a dict.

```python
cost = client.metadata.get_cost(
    dataset="ARCX.PILLAR",
    symbols=["SLV"],
    schema="ohlcv-1m",
    start="2020-01-01",
    end="2025-11-28",
)
# cost is a float, e.g. 0.54
```
