Venues and datasets

# CME Globex MDP
3.0

**Dataset ID** : [GLBX.MDP3](https://databento.com/catalog/cme/GLBX.MDP3)

The CME Group disseminates full depth-of-book data for CME, CBOT, NYMEX, and
COMEX through their MDP 3.0 feed, which Databento receives over UDP multicast
in our DC3 datacenter. Databento provides full coverage of all futures and
options on futures beginning in June 2010.

In May 2017, CME introduced MDP 3.0, which provides full granularity for every
order event (MBOFD) in addition to providing aggregated depth at a limited
number of price levels (MBP). Databento's data is based on the MBOFD feed; we
do not normalize CME's incremental MBP messages.

Before May 2017, data was based on the legacy FIX/FAST protocol.

CME makes the specifications of their MDP 3.0 feed available [on their website
here](https://www.cmegroup.com/confluence/display/EPICSANDBOX/CME+MDP+3.0+Market+Data).

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Timestamps![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

CME MDP 3.0 messages received by Databento will generally include two
timestamps with nanosecond precision. As with all of our datasets, we also
collect a `ts_recv` timestamp when the packet was received by our capture
server.

Databento Field | MDP 3.0 Tag | Description  
---|---|---  
`ts_recv` | N/A | The capture-server-received timestamp.  
`ts_event` | 60-TransactTime | The matching-engine-received timestamp.  
`ts_in_delta` | 52-SendingTime | The matching-engine-sending timestamp.  
  
More details about our timestamps are available in our [timestamping
guide](/docs/architecture/timestamping-guide).

## MBO
normalization

**`F_LAST` flag in MBO**

When interpreting MBO data, the `F_LAST` [flag](/docs/standards-and-
conventions/common-fields-enums-types#flags) (`0x80`, 128) is used to mark the
last record in a single event for each `instrument_id`. This flag is based on
CME's "End of event" flag in tag 5799-MatchEventIndicator. However, CME only
sets this flag on one single message, even if the event spanned multiple
instruments. Instead, we set the `F_LAST` flag on the last record for _each_
instrument, so that the data can be interpreted consistently with any subset
of instruments. Records with `action` **N** one may carry `F_LAST`.

Across all of Databento's feeds, it is important to use the `F_LAST` flag when
calculating the best bid and offer, like our MBP-1 schema. Between records
_without_ the `F_LAST` flag, the book is in the process of updating, and the
apparent best bid and offer may have already been traded.

This is accounted for when we construct our other schemas, such as MBP-1 and
Trades. Outside of MBO, this flag can be ignored.

**Order events**

Records in the [MBO schema](/docs/schemas-and-data-formats/mbo) with **A** dd,
**M** odify, and **C** ancel actions are normalized from [Market Data
Incremental Refresh -
MBOFD](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+MBOFD),
and records with **T** rade and **F** ill actions are normalized from [Market
Data Incremental Refresh - Trade
Summary](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+Trade+Summary)
messages, and records with the clea**R** action are normalized from [Market
Data Incremental Refresh - Channel
Reset](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+Channel+Reset)
messages.

**Trade events**

The MBO schema includes detailed information about both sides of the trade by
normalizing [Market Data Incremental Refresh - Trade
Summary](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+Trade+Summary)
messages from CME into **T** rade and **F** ill actions. The [MBP-n and trades
schemas](/docs/schemas-and-data-formats/mbp-1) include **T** rade records, but
do not include **F** ill detail records.

Each CME MDP 3.0 [Market Data Incremental Refresh - Trade
Summary](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+Trade+Summary)
message from CME contains two repeating groups: a set of _trade summaries_ ,
followed by a set of _order ID entries_. Each trade summary in the message is
normalized into one **T** rade record. If there is a defined aggressor for the
trade, its corresponding order ID entry from the second set is used to
populate the `order_id` field. This commonly happens when an incoming order
partially executes, leaving the remaining quantity to rest on the book.

Following each **T** rade record, the remaining (passive) order ID entries are
normalized into **F** ill records. These records correspond to resting orders
in the order book, but **do not** modify the orders.

If the aggressing order existed on the book, then a **F** ill record will be
emitted for it. This can happen if a resting order was modified to cross the
book. If the aggressing order did not already exist on the book then its **F**
ill record is suppressed, however its order ID is still reported in the **T**
rade record as described above.

**Side**

[Implied
trades](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Implied+Book)
may not have an aggressing side set. These trades will normalized with
[side](/docs/standards-and-conventions/common-fields-enums-types#side) set to
**N** one.

**Order Priority**

Although Databento does not expose tag 37707-MDOrderPriority in the MBO
schema, first in, first out priority (FIFO) can be determined from message
order. Messages for a single `instrument_id` are never reordered, even if they
have the same timestamps. Our daily MBO snapshots also preserve FIFO order
priority.

**CME MBO snapshot**

At the beginning of the weekly trading session, CME publishes a MBO order
snapshot. This snapshot contains any orders that persisted from the previous
trading session, such as Good 'Till Cancelled orders. The event timestamp
(`ts_event`) for these orders corresponds to the time the snapshot is
generated by CME. It does not reflect the original time the order was entered
or last modified.

These orders are not published by CME in priority order. As Databento does not
expose tag 37707-MDOrderPriority, if these MBO records were published by
Databento in the same order they were sent from CME, it would not be possible
to determine the correct order priority. In order to publish the records in
priority order, Databento buffers the orders from the first CME event of the
trading session, per instrument.

Once the End of Event is reached, Databento sorts the records in priority
order and then publishes them.

Therefore, the order priority is correctly reflected by the order of
publishing.

The `ts_recv` for these records will be set to the `ts_recv` of the final
record of the event. As the records have been slightly delayed and the
`ts_recv` of the initial records may not match the original `ts_recv`, the
flag `F_BAD_TS_RECV` is set on these records.

The `F_SNAPSHOT` flag is also set on these records to indicate that they are
from the CME snapshot.

**Weekly Sessions**

Although CME pauses trading daily, the market follows a weekly session
structure with most instruments accepting orders from Sunday night to Friday
night (local time). To make it easier to work with historical MBO data,
Databento includes an [order book snapshot](/docs/standards-and-
conventions/mbo-snapshot) at 00:00:00 UTC each weekday (Monday-Friday).

Before the trading session begins, or during the daily pause, it is normal for
order books to be locked or crossed: CME will accept orders but won't execute
any trades until the opening uncrossing.

Details about the trading hours and sessions for each product group are
available on the [Trading hours](https://www.cmegroup.com/trading-hours.html)
page of CME's website.

## Statistics
normalization

Databento normalizes the following daily statistics from [Market Data
Incremental Refresh - Daily
Statistics](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+Daily+Statistics):

  * Settlement price
  * Cleared volume
  * Open interest
  * Fixing price

The exact times these statistics are published will vary. Cleared volume and
open interest statistics will normally publish on the following UTC date,
unless they are for Friday's session, in which case they will be published on
the following Sunday. Settlement price statistics will first publish shortly
after the [settlement
window](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457085528/Daily+Settlement+Time+Details)
for an instrument. The CME does not publish settlement prices on their MDP
feed for instruments without open interest or volume.

Databento normalizes the following session statistics from [Market Data
Incremental Refresh - Session
Statistics](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+Data+Incremental+Refresh+-+Session+Statistics):

  * Opening and indicative opening price
  * Trading session high and low price
  * Trading session highest bid and lowest offer

These statistics are published throughout the trading session as the values
change.

**Reference timestamp (`ts_ref`)**

CME includes tag 5796-TradingReferenceDate, the trading session date, with
daily statistics. This field is normalized to `ts_ref` as a nanosecond-
precision UNIX timestamp for consistency with other timestamps. Because the
source only has date precision, users should avoid localizing `ts_ref` to
their time zone.

**Multiple records for the same date**

CME often publishes multiple messages for the same trading session date
(indicated by `ts_ref`) and daily statistic. The initial messages indicate
preliminary values, and the final message for the trading session date
indicates the final value. The final message is always the most accurate and
the one you should use, when available.

**Stat flags**

Settlement price statistics normalize the [bit
field](https://en.wikipedia.org/wiki/Bit_field) [tag
731-SettlPriceType](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Settlement+Price)
to the `stat_flags` field.

Value | Decimal | Description  
---|---|---  
`1 << 0` | 1 | Whether the settlement price is final, as opposed to preliminary.  
`1 << 1` | 2 | Whether the settlement price is actual, as opposed to theoretical.  
`1 << 2` | 4 | Some products have a different trading tick size than their clearing tick size. 1 if settling at the trading tick and 0 if at the clearing tick.  
`1 << 3` | 8 | Whether the price is an intraday settlement price and disseminated before the official end-of-day settlement calculation.  
  
[The CME documentation on settlement
prices](https://www.cmegroup.com/confluence/display/EPICSANDBOX/Settlement+Prices#SettlementPrices-
SettlementatTradingTick/SettlementatClearingTick) expands on the meaning of
each flag value.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Symbology![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Databento normalizes the `asset` field from tag 6937-Asset of the [Market Data
Security
Definition](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457672532/MDP+3.0+-+Security+Definition)
message. The `asset` field is used when requesting data with [parent
symbology](/docs/standards-and-conventions/symbology#parent).

Databento normalizes the `raw_symbol` field from tag 55-Symbol of the Security
Definition message. This symbol contains the product code, followed by the
[month code](https://www.cmegroup.com/month-codes.html), followed by the year.
Previously, this field would only contain a 1-digit year, e.g. `ESZ3` for the
E-mini S&P 500 December 2023 contract. In some recently listed contracts, CME
has begun using a 2-digit year, e.g. `NGN25` for the Henry Hub Natural Gas
July 2025 contract.

## Spreads and
combos

In addition to futures and options on futures, this dataset also contains data
for both exchange-listed spreads and [user-defined spreads
(UDS)](https://www.cmegroup.com/confluence/display/EPICSANDBOX/User+Defined+Spread+-+UDS).

Spreads and combinations consisting only of futures (such as exchange-listed
calendar spreads) are considered part of the futures [parent
symbols](/docs/standards-and-conventions/symbology#parent). For example,
`ESH4-ESZ4` is a calendar spread contained in `ES.FUT`. These spreads can have
negative prices.

Spreads and combinations containing options (such as butterfly or covered
spreads) are considered part of the option [parent symbols](/docs/standards-
and-conventions/symbology#parent). For example, `UD:1V: VT 2533938` is a
vertical option spread contained in `ES.OPT`.

[The CME documentation on spreads and
combinations](https://www.cmegroup.com/confluence/display/EPICSANDBOX/Spreads+and+Combinations+Available+on+CME+Globex)
contains an exhaustive list of the different spread types. The
`instrument_class` field in the definition schema can be used to identify
spread instruments generically. The `SecuritySubType` field in the exchange
documentation is normalized to the `secsubtype` field in the `definition`
schema and can be used to identify the exchange-specific spread type.

To identify the parent symbol of strategies and user-defined spreads, the
`asset` field in the definition schema should be used.

## Status
normalization

Status records are normalized from [Market Data Security
Status](https://cmegroupclientsite.atlassian.net/wiki/x/JbtAGw) messages. A
single status message from CME may the update status of all instruments in a
`group`, all instruments in a `group` and `asset`, or an individual
instrument. To support requesting data for individual instruments, Databento
normalizes all these messages into instrument-level status messages. When
requesting status schema data for multiple instruments in the same `group`,
there will be status records that are identical minus the `instrument_id`.

The state fields of the status schema: `is_trading`, `is_quoting`, and
`is_short_sell_restricted` are based solely on the status updates from CME.
Group-level status updates generate a status record for each instrument with a
definition, regardless of activation or expiration, as a result, before
activation the status state fields may be incorrect.

## Matching
Algorithms

CME implements various [matching algorithms](/docs/schemas-and-data-
formats/instrument-definitions#matching-algorithm) across its universe of
products, with **F** IFO being the most common.

Specific details on the different algorithms can be found on the [Supported
Matching Algorithms
page](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457218479/Supported+Matching+Algorithms)
on the CME website.

## MDP 2
data

On 2017-05-21, CME introduced full granularity for every order event (MBOFD).
Data prior to this date is based on the MDP 2, level-aggregated FIX protocol.
This leads to some differences in the data.

**Timestamps**

MDP 2 data is sourced from FIX flat files. Because of this, capture timestamps
(commonly available as `ts_recv`) are not available. The `ts_recv` field on
all MDP 2 data is set to the same value as `ts_event`.

Because of this, all records on MDP 2 data have the `F_BAD_TS_RECV` flag set.
This is normal and expected.

Nanosecond-resolution timestamps have been introduced by CME on 2015-11-20.
Timestamps prior to that date are limited to millisecond resolution.

Databento Field | CME Tag | Description  
---|---|---  
`ts_recv` | 52-SendingTime | The matching-engine-sending timestamp.  
`ts_event` | 52-SendingTime | The matching-engine-sending timestamp.  
  
**MBO format**

MDP 2 data predates the introduction of full-depth MBO by CME. Because of
this, the MBO schema is not available. The highest granularity schema
available for MDP 2 data is MBP-10.

**Status schema**

There's significantly less status data prior to 2015, this is expected. Before
November 2015, status changes from the normal trading schedule did not result
in a status message.

