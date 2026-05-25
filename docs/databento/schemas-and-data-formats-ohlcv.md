Schemas and data formats

# Aggregate bars
(OHLCV)

**Aggregate bars (OHLCV)** provide open, high, low, and close prices and total
volume aggregated from trades at 1-second, 1-minute, 1-hour, or 1-day
intervals.

Databento adopts the following convention for OHLCV:

  * The time interval is indicated by the schema ID's suffix: `-1s` for 1-second, `-1m` for 1-minute, `-1h` for 1-hour, and `-1d` for 1-day.
  * The `ts_event` timestamp marks the start of each interval.
  * If no trade occurs within the interval, no record is printed.

## Fields
(`ohlcv-1s`, `ohlcv-1m`, `ohlcv-1h`, `ohlcv-1d`)![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Field | Type | Description  
---|---|---  
`ts_event` | uint64_t | The inclusive start time of the bar aggregation period based on the `ts_recv` from trade messages expressed as the number of nanoseconds since the UNIX epoch.  
`rtype` | uint8_t | A sentinel value indicating the record type. Can be 32 (`OHLCV-1s`), 33 (`OHLCV-1m`), 34 (`OHLCV-1h`), or 35 (`OHLCV-1d`). See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`open` | int64_t | The open price for the bar where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`high` | int64_t | The high price for the bar where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`low` | int64_t | The low price for the bar where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`close` | int64_t | The close price for the bar where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`volume` | uint64_t | The total volume traded during the aggregation period.  
  
## Other sampling
intervals

If you need other sampling intervals, we recommend that you construct OHLCV
aggregates from `trades` data or subsample the OHLCV schema with the nearest
resolution on client side. For example, if you need 5-minute OHLCV aggregates,
you can subsample this from `ohlcv-1m`.

## Best
practices

It's recommended that you construct OHLCV aggregates yourself from `trades`
data if your application can handle the larger volume of data. There will
always be slight implementation differences in how a vendor constructs their
OHLCV aggregates. These differences include how trade conditions and
retroactive trade breaks are handled, which timestamp is used for the event,
and how precisely a vendor closes or publishes the aggregate bar after the end
of the interval. Daily OHLCV aggregates may also differ by whether best bid or
offer prices are used to compute the highs and lows, and whether the volumes
should include block trades.

Constructing OHLCV aggregates yourself ensures additional transparency into
how the aggregates are computed and consistency within your data.

Our `ohlcv-1d` schema is based on UTC dates. If you are interested in daily
data based on exchange session hours, you may need to request data for a more
granular OHLCV schema and aggregate the data yourself.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> `ohlcv-1d` is based entirely on prices and volumes from the electronic
> trading session and will be consistent with prices and volumes observed from
> aggregating our `trades` data. However, this may differ from official
> settlement prices and volumes published by the venue, which could include
> block trades, OTC transactions, and other effects. For official settlement
> data and more details about this, see our [statistics](/docs/schemas-and-
> data-formats/statistics) schema.

