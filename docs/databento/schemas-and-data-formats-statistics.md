Schemas and data formats

# ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Statistics![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

**Statistics** provides official summary statistics of each instrument that's
published by the venue. This generally includes properties like daily volume,
open interest, preliminary and final settlement prices, and official open,
high, and low prices.

## Fields
(`statistics`)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The capture-server-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_recv](/docs/standards-and-conventions/common-fields-enums-types#ts-recv).  
`ts_event` | uint64_t | The matching-engine-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Always 24 in the statistics schema. See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`ts_ref` | uint64_t | The reference timestamp expressed as the number of nanoseconds since the UNIX epoch.  
`price` | int64_t | The value for price statistics where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`quantity` | int64_t | The value for non-price statistics. Will be `INT64_MAX` when unused.  
`sequence` | uint32_t | The message sequence number assigned at the venue.  
`ts_in_delta` | int32_t | The matching-engine-sending timestamp expressed as the number of nanoseconds before `ts_recv`. See [ts_in_delta](/docs/standards-and-conventions/common-fields-enums-types#ts-in-delta).  
`stat_type` | uint16_t | The type of statistic value contained in the message. See [Types of statistics](/docs/schemas-and-data-formats/statistics#types-of-statistics) table below.  
`channel_id` | uint16_t | The channel ID within the venue.  
`update_action` | uint8_t | Indicates if the statistic is newly added (`1`) or deleted (`2`). (Deleted is only used with some stat_types)  
`stat_flags` | uint8_t | Additional flags associated with certain stat types and datasets. Refer to the [Venues and datasets](/docs/venues-and-datasets) section for details.  
  
Some fields are not applicable depending on the type of statistic. Null,
invalid or inapplicable values are represented by the maximum value of the
field's type. For example, null is represented by `2^64-1` for `ts_event`,
which has an unsigned 64-bit integer type. In cases where the value is
actually zero or null has the same meaning as zero, zero is used instead.

## Types of
statistics

Type | `stat_type` | `price` | `quantity` | `stat_flags` | `ts_ref` | Description  
---|---|---|---|---|---|---  
Opening price | `1` | ✓ |  |  |  | The price of the first trade of an instrument.  
Indicative opening price | `2` | ✓ | ✓ |  |  | The probable price of the first trade of an instrument published during pre-open.  
Settlement price | `3` | ✓ |  | ✓ | ✓ | The settlement price of an instrument. Flags will indicate whether the price is final or preliminary and actual or theoretical.  
Trading session low price | `4` | ✓ |  |  |  | The lowest trade price of an instrument during the trading session.  
Trading session high price | `5` | ✓ |  |  |  | The highest trade price of an instrument during the trading session.  
Cleared volume | `6` |  | ✓ |  | ✓ | The number of contracts cleared for an instrument on the previous trading date.  
Lowest offer | `7` | ✓ |  |  |  | The lowest offer price for an instrument during the trading session.  
Highest bid | `8` | ✓ |  |  |  | The highest bid price for an instrument during the trading session.  
Open interest | `9` |  | ✓ |  | ✓ | The current number of outstanding contracts of an instrument.  
Fixing price | `10` | ✓ |  |  | ✓ | The volume-weighted average price (VWAP) for a fixing period.  
Close price | `11` | ✓ |  |  |  | The last trade price and quantity during a trading session.  
Net change | `12` | ✓ |  |  |  | The change in price from the close price of the previous session to the most recent close price.  
Volume-weighted average price | `13` | ✓ | ✓ |  |  | The volume-weighted average price (VWAP) during the trading session.  
Volatility | `14` | ✓ |  |  | ✓ | The implied volatility associated with the settlement price.  
Delta | `15` | ✓ |  |  | ✓ | The options delta associated with the settlement price.  
Uncrossing price | `16` | ✓ |  |  |  | The auction uncrossing price and quantity. This is used for auctions that are neither the official opening auction nor the official closing auction.  
Upper price limit | `17` | ✓ |  |  |  | The upper price limit for an instrument for the trading session.  
Lower price limit | `18` | ✓ |  |  |  | The lower price limit for an instrument for the trading session.  
Block volume | `19` |  | ✓ |  |  | The total number of block contracts traded for the day.  
Venue-specific volume 1 | `10001` |  | ✓ |  |  | A venue-specific volume field. Refer to venue documentation for details.  
  
## Types of
statistics by dataset![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

The table below shows which statistics are available by dataset ID. See the
table above for a list of `stat_type` variants.

`stat_type` | `1` | `2` | `3` | `4` | `5` | `6` | `7` | `8` | `9` | `10` | `11` | `12` | `13` | `14` | `15` | `16` | `17` | `18` | `19` | `10001`  
---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---  
[ARCX.PILLAR](/docs/venues-and-datasets/arcx-pillar) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  | ✓ |  |  |  |   
[BATS.PITCH](/docs/venues-and-datasets/bats-pitch) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  |  |  |  |  |   
[EQUS.SUMMARY](/docs/venues-and-datasets/equs-summary) | ✓ |  |  | ✓ | ✓ | ✓ |  |  |  |  | ✓ |  |  |  |  |  |  |  |  |   
[GLBX.MDP3](/docs/venues-and-datasets/glbx-mdp3) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |  |  |  |  |  |  |  |   
[IFEU.IMPACT](/docs/venues-and-datasets/ifeu-impact) | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |  | ✓ | ✓ | ✓ |  |  |  |  |   
[IFLL.IMPACT](/docs/venues-and-datasets/ifll-impact) | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |  | ✓ | ✓ | ✓ |  |  |  |  |   
[IFUS.IMPACT](/docs/venues-and-datasets/ifus-impact) | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |  | ✓ | ✓ | ✓ |  |  |  |  |   
[NDEX.IMPACT](/docs/venues-and-datasets/ndex-impact) | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |  | ✓ | ✓ | ✓ |  |  |  |  |   
[OPRA.PILLAR](/docs/venues-and-datasets/opra-pillar) | ✓ |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  | ✓ | ✓ |  |  |  |  |  |  |  |   
[XASE.PILLAR](/docs/venues-and-datasets/xase-pillar) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  | ✓ |  |  |  |   
[XCBF.PITCH](/docs/venues-and-datasets/xcbf-pitch) | ✓ |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  | ✓ |  |  |  |  |  | ✓ | ✓ | ✓ | ✓  
[XCHI.PILLAR](/docs/venues-and-datasets/xchi-pillar) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  | ✓ |  |  |  |   
[XEEE.EOBI](/docs/venues-and-datasets/xeee-eobi) | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |  |  |  |  |  |  |  |  |   
[XEUR.EOBI](/docs/venues-and-datasets/xeur-eobi) | ✓ |  | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |  |  |  |  |  |  |  |  |   
[XNAS.BASIC](/docs/venues-and-datasets/xnas-basic) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  |  |  |  |  |   
[XNAS.ITCH](/docs/venues-and-datasets/xnas-itch) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  | ✓ |  |  |  |   
[XNYS.PILLAR](/docs/venues-and-datasets/xnys-pillar) | ✓ |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |  | ✓ |  |  |  |   
  
## Official vs.
Databento summary statistics![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

The key distinction of the `statistics` schema is these are official summary
statistics provided by the venue—Databento doesn't compute these statistics.

On most venues, Databento also provides separate [OHLCV](/docs/schemas-and-
data-formats/ohlcv) data that could be used in place of official open, high,
low, and settlement prices, and volume from the `statistics` schema.

These schemas are different and may vary for a few intentional reasons:

  * Often, official statistics have opaque methodology or are difficult to replicate because they're tallied by hand
  * Some venues include volumes and open interest from open outcry, auction, block trades, RFQs, or other events that may not be disseminated in the electronic trading session or public feeds
  * Some venues double count volumes

The main purpose of Databento's summary data is that it provides more
consistency: we derive it systematically from the tick data or full order book
data during the electronic trading session and ensures consistency with our
tick data; we publish at more deterministic times, and we normalize across
venues using a UTC midnight cutoff universally. For electronic trading and
research applications, this consistency may be more important, whereas for
back office and administrative applications, official statistics may be
preferable.

