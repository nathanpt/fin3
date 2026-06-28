# Databento Dataset Comparison for US Equities OHLCV

Audited 2025-05-26. Costs are usage-based (pay-per-request), not subscription.

## Cost Comparison

### 8 critical symbols (SLV, SMCI, MSTR, META, AAPL, MARA, RIOT, XLRE)

| Dataset | Schema | Date Range | Cost |
|---|---|---|---|
| ARCX.PILLAR | ohlcv-1m | 2018-05 to 2025-11 | $4.36 |
| XNAS.ITCH | ohlcv-1m | 2020-01 to 2025-11 | $3.83 |
| XNAS.BASIC | ohlcv-1m | 2024-07 to 2025-11 | $1.37 |
| EQUS.SUMMARY | ohlcv-1d | 2024-07 to 2025-11 | $0.0044 |

### Full 60 symbols

| Dataset | Schema | Date Range | Cost |
|---|---|---|---|
| ARCX.PILLAR | ohlcv-1m | 2018-05 to 2025-11 | $34.87 |
| XNAS.ITCH | ohlcv-1m | 2020-01 to 2025-11 | $27.32 |
| XNAS.BASIC | ohlcv-1m | 2024-07 to 2025-11 | $8.02 |
| EQUS.SUMMARY | ohlcv-1d | 2024-07 to 2025-11 | $0.0324 |

## Dataset Details

### XNAS.ITCH (Nasdaq TotalView) -- current default

- **Coverage**: Nasdaq trades only. No off-exchange (TRF) trades.
- **History**: From 2020-01 (OHLCV aggregates), full depth from 2007.
- **Problem**: Only captures trades executed on Nasdaq. Symbols primarily traded
  on NYSE Arca (SLV, XLRE, etc.) have 10-20% null bars because many minutes
  have zero Nasdaq prints despite high consolidated volume.
- **Schemas**: ohlcv-1m, ohlcv-1d, ohlcv-1h, ohlcv-1s, MBO, MBP-1, MBP-10, trades, etc.

### ARCX.PILLAR (NYSE Arca Integrated) -- recommended for 1m

- **Coverage**: NYSE Arca full depth of book. NYSE Arca is the leading exchange
  for ETF listing and trading (~10% of all US equities ADV). 71.1% of time at NBBO
  for all US ETFs.
- **History**: From 2018-05.
- **Advantage**: Captures Arca trades for ETFs (SLV, XLRE, XLU, etc.) where XNAS.ITCH
  has sparse data. Also captures many large-cap names that trade on Arca.
- **Schemas**: ohlcv-1m, ohlcv-1d, ohlcv-1h, ohlcv-1s, MBO, MBP-1, MBP-10, trades, imbalance, etc.
- **Symbol convention**: CMS (e.g. `BRK B`), not Nasdaq convention (`BRK.B`).

### XNAS.BASIC (Nasdaq Basic with NLS Plus)

- **Coverage**: Includes off-exchange trades reported to FINRA/Nasdaq TRFs.
  Captures majority of non-lit market volume.
- **History**: From 2024-07 only. Not suitable for historical backfill before that.
- **Schemas**: ohlcv-1m, ohlcv-1d, ohlcv-1h, ohlcv-1s, trades, BBO-1s, BBO-1m, etc.
- **Note**: Would be a good supplement to ARCX.PILLAR for recent data, but limited history.

### EQUS.SUMMARY (Databento US Equities Summary)

- **Coverage**: Consolidated across all 15 NMS exchanges + 30 ATSs. 100% intraday
  volume on a delayed basis, consolidated end-of-day prices.
- **History**: From 2024-07 only.
- **Schemas**: ohlcv-1d, definition, statistics. **No ohlcv-1m.** Not viable for 1m data.
- **Cost**: Extremely cheap for daily data.
- **Note**: Ideal for daily bars, useless for intraday.

## Subscription Pricing (for reference)

The Standard plan ($199/month) includes unlimited access to:
- 7 years of OHLCV history across all equities datasets
- 12 months of L0/L1 history
- 1 month of L2 (MBP-10) and L3 (MBO) history
- Live data with no exchange license fees

All datasets above are part of the Databento US Equities service.

## Null Bar Root Cause

The 17% null rate on SLV (and similar rates on XLRE, SMCI, MSTR, META) in
`equities-1m-databento` is caused by using `XNAS.ITCH`, which only reports bars
when trades execute on Nasdaq. SLV trades ~35M shares/day consolidated but only
a fraction on Nasdaq. Individual minutes with zero Nasdaq prints produce null
OHLCV bars.

Re-downloading from `XNAS.ITCH` does not help (verified by deleting and
re-downloading SLV -- identical 17% null rate).

## Cost API

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
