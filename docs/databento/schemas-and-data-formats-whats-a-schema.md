Schemas and data formats

# What's a
schema?

Databento supports over 15 different data formats of market data. When you
make a request from Databento, you must usually specify which data format—also
called a **schema** —to receive your data in.

## Supported
schemas and their fields![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

A schema represents a collection of data fields. The following is a summary of
schemas supported by Databento. Click on any schema below for its details, the
fields included, and a data dictionary that defines each field.

Schema | Schema IDs | Common names used by third parties  
---|---|---  
[MBO](/docs/schemas-and-data-formats/mbo) | `mbo` | L3, Market by order, full order book, tick data  
[MBP-10](/docs/schemas-and-data-formats/mbp-10) | `mbp-10` | L2, Market by price, market depth  
[MBP-1](/docs/schemas-and-data-formats/mbp-1) / [CMBP-1](/docs/schemas-and-data-formats/mbp-1#consolidated-market-by-price-cmbp-1) | `mbp-1` / `cmbp-1` | L1, Top of book, trades and quotes  
[BBO](/docs/schemas-and-data-formats/bbo) / [CBBO](/docs/schemas-and-data-formats/bbo#consolidated-bbo-on-interval-cbbo) | `bbo-1s`, `bbo-1m` / `cbbo-1s`, `cbbo-1m` | L1, Top of book sampled in time space, subsampled BBO and trades  
[TBBO](/docs/schemas-and-data-formats/tbbo) / [TCBBO](/docs/schemas-and-data-formats/tbbo#consolidated-bbo-on-trade-tcbbo) | `tbbo` / `tcbbo` | L1, Top of book sampled in trade space  
[Trades](/docs/schemas-and-data-formats/trades) | `trades` | L1, Last sale, time and sales, tick-by-tick trades  
[OHLCV](/docs/schemas-and-data-formats/ohlcv) | `ohlcv-1s`, `ohlcv-1m`, `ohlcv-1h`, `ohlcv-1d` | L0, OHLCV bars, aggregates  
[Definition](/docs/schemas-and-data-formats/instrument-definitions) | `definition` | L0, Security definitions, reference data, symbol list  
[Imbalance](/docs/schemas-and-data-formats/imbalance) | `imbalance` | L3, Auction imbalance, order imbalance, NOII  
[Statistics](/docs/schemas-and-data-formats/statistics) | `statistics` | L0, Session or daily statistics, end-of-day summary, open interest  
[Status](/docs/schemas-and-data-formats/status) | `status` | L0, Market or trading state/status  
  
**Market by order (MBO)** provides every order book event across every price
level, keyed by its order ID. This allows you to determine the queue position
of each order and provides the highest level of granularity available.

**Market by price (MBP-10)** provides every order book event across the top
ten price levels, keyed by price. This includes every trade and changes to
aggregate market depth, alongside total size and order count at the top ten
price levels.

**Market by price (MBP-1)** provides every order book event that updates the
top price level, also known as the best bid and offer (BBO). This includes
every trade and changes to book depth, alongside total size and order count at
the BBO.

**Consolidated market by price (CMBP-1)** provides every order book event that
updates the top price level across all venues in the dataset, also known as
the consolidated best bid and offer (CBBO). This includes every trade and
changes to book depth, alongside total size and publisher attribution at the
CBBO.

**BBO on trade (TBBO)** provides every trade event alongside the BBO
immediately  _before_ the effect of each trade. This is a subset of
[MBP-1](/docs/schemas-and-data-formats/mbp-1).

**Consolidated BBO on trade (TCBBO)** provides every trade event alongside the
consolidated BBO immediately before the effect of each trade. This is a subset
of [CMBP-1](/docs/schemas-and-data-formats/mbp-1#consolidated-market-by-price-
cmbp-1).

**BBO on interval (BBO)** provides the last best bid, best offer, and sale at
1-second or 1-minute intervals. This is a subset of [MBP-1](/docs/schemas-and-
data-formats/mbp-1).

**Consolidated BBO on interval (CBBO)** provides the consolidated last best
bid, best offer, and sale at 1-second or 1-minute intervals. This is a subset
of [CMBP-1](/docs/schemas-and-data-formats/mbp-1#consolidated-market-by-price-
cmbp-1).

**Trades** provides every trade event. This is a subset of
[MBO](/docs/schemas-and-data-formats/mbo).

**Aggregate bars (OHLCV)** provide open, high, low, and close prices and total
volume aggregated from trades at 1-second, 1-minute, 1-hour, or 1-day
intervals.

**Instrument definitions** provide reference information about each
instrument, which includes properties like symbol, instrument name, expiration
date, listing date, tick size, and strike price.

**Imbalance** provides auction imbalance data such as paired quantity, total
quantity, and auction status.

**Statistics** provides official summary statistics of each instrument that's
published by the venue. This generally includes properties like daily volume,
open interest, preliminary and final settlement prices, and official open,
high, and low prices.

**Status** provides updates about the trading session, such as halts, pauses,
short-selling restrictions, auction start, and other matching engine statuses.
The granularity and frequency of these updates vary by publisher and dataset.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> The MBP-1, BBO and TBBO schemas, as well as the CMBP-1, CBBO, and TCBBO
> schemas, all provide top of book data with different update space and
> sampling intervals. Learn more about their differences in our [MBP-1 vs. BBO
> vs. TBBO schemas guide](/docs/faqs/difference-between-mbp-and-tbbo).

## Why are
Databento's naming conventions different from third
parties?

Databento avoids terms like Level 1 (L1) or Level 2 (L2) due to their
inconsistent application. For example, some vendors refer to both MBO and MBP
data as L2, while others strictly refer to MBP data as L2. More misleadingly,
many vendors refer to MBO data as L3, even though this doesn't follow any
major trading venue's naming convention.

Likewise, the term **tick** originates from the concept of a ticker tape and
only refers to trades, _not_ resting limit orders. This becomes a source of
confusion when vendors use the term **tick data** to refer to either MBO or
MBP data when it should be strictly reserved for trades data.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> For more Databento naming conventions and key terminology, visit our
> [FAQs](/docs/faqs).

## Special
cases

Our MBO, MBP-1 and MBP-10 schemas adopt the following conventions in these
special cases:

  * **Combining MBO with trades feed** : Typically, MBO data provides the highest granularity, but certain venues enhance their trades feed with additional information like trades not reflected in the MBO feed, consolidated NBBO, and more. In these cases, we document the exception in our [Venues and datasets](/docs/venues-and-datasets) section and recommend that you request _both_ our MBO and trades schemas if you need the highest level of granularity.

## Deriving one
schema from another

Databento captures market data directly from the source and is only subscribed
to the most granular feed(s) available from each [publisher](/docs/venues-and-
datasets). Order book feeds are usually [normalized](/docs/standards-and-
conventions/normalization) into our MBO schema and top-of-book feeds are
usually normalized into our MBP-1 schema.

To ensure consistency between schemas, Databento doesn't source the less
granular schemas from separate feeds. Instead, Databento derives all of the
less granular schemas starting from the most granular schema available. As you
may have noticed from our schema's descriptions, the majority of them—MBP,
BBO, TBBO, trades, and OHLCV—are simply derived from MBO data.

Likewise, you can also derive one schema from another losslessly on the client
side, and you should expect your self-derived data to be consistent with ours.
For example:

  * MBP-1, BBO, and Trades can be derived from MBP-10.
  * BBO, TBBO, and Trades can be derived from MBP-1.
  * Trades and OHLCV can be derived from TBBO.
  * OHLCV can be derived from Trades.

Deriving your own schema is useful for various reasons:

  * The data needs to be defined differently for your application.
  * Our derivation differs from those of another vendor and you want transparency.
  * You can cut down the number of API requests made to Databento by getting the most granular schema that you need and deriving the rest yourself.
  * Databento provides MBP-10 merely as a convenience feature. You can reduce bandwidth requirements, latency, and transfer time significantly by deriving MBP-10 yourself from MBO.

This is especially relevant for OHLCV, which can vary depending on how trade
breaks or market halts are managed, how the start and end of each time
interval are determined, and how illiquid instruments are handled if there are
no trades over a given time interval. If these considerations are trivial for
your use case, Databento offers OHLCV data in multiple time intervals
(seconds, minutes, hours, and daily) for your convenience.

The table below summarizes which schemas can be derived from the another. Each
row represents the original schema, and each column represents schemas that
you can derive from the original schema.

Schema | MBO | MBP-10 | MBP-1 | CMBP-1 | TBBO | TCBBO | BBO-1s | BBO-1m | CBBO-1s | CBBO-1m | Trades | OHLCV-1s | OHLCV-1m | OHLCV-1h | OHLCV-1d  
---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---  
MBO | ✓ | ✓ | ✓ |  | ✓ |  | ✓ | ✓ |  |  | ✓ | ✓ | ✓ | ✓ | ✓  
MBP-10 |  | ✓ | ✓ |  | ✓ |  | ✓ | ✓ |  |  | ✓ | ✓ | ✓ | ✓ | ✓  
MBP-1 |  |  | ✓ |  | ✓ |  | ✓ | ✓ |  |  | ✓ | ✓ | ✓ | ✓ | ✓  
CMBP-1 |  |  |  | ✓ |  | ✓ |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓  
TBBO |  |  |  |  | ✓ |  |  |  |  |  | ✓ | ✓ | ✓ | ✓ | ✓  
TCBBO |  |  |  |  |  | ✓ |  |  |  |  | ✓ | ✓ | ✓ | ✓ | ✓  
BBO-1s |  |  |  |  |  |  | ✓ | ✓ |  |  |  |  |  |  |   
BBO-1m |  |  |  |  |  |  |  | ✓ |  |  |  |  |  |  |   
CBBO-1s |  |  |  |  |  |  |  |  | ✓ | ✓ |  |  |  |  |   
CBBO-1m |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  |   
Trades |  |  |  |  |  |  |  |  |  |  | ✓ | ✓ | ✓ | ✓ | ✓  
OHLCV-1s |  |  |  |  |  |  |  |  |  |  |  | ✓ | ✓ | ✓ | ✓  
OHLCV-1m |  |  |  |  |  |  |  |  |  |  |  |  | ✓ | ✓ | ✓  
OHLCV-1h |  |  |  |  |  |  |  |  |  |  |  |  |  | ✓ | ✓  
OHLCV-1d |  |  |  |  |  |  |  |  |  |  |  |  |  |  | ✓  
  
> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> Learn how to resample trades data to other intervals, such as 5-minute
> intervals, from our [bar aggregation tutorial](/docs/examples/basics-
> historical/custom-ohlcv).
>
> You can also learn how to generate MBP-10 from MBO data using an order book,
> as seen in our [limit order book construction
> tutorial](/docs/examples/order-book/limit-order-book).

