Schemas and data formats

# BBO on interval
(BBO)

**BBO on interval (BBO)** provides the last best bid, best offer, and sale at
1-second or 1-minute intervals. This is a subset of [MBP-1](/docs/schemas-and-
data-formats/mbp-1).

Databento adopts the following convention for BBO:

  * The time interval is indicated by the schema ID's suffix: `-1s` for 1-second and `-1m` for 1-minute.
  * If no trade or BBO update occurs within the interval, no record is printed.
  * If a BBO update occurs but no trade takes place within the interval, the last sale information is forward-filled from the previous interval.
  * If no BBO update occurs but a trade takes place within the interval, the BBO information is forward-filled from the previous interval.

## Fields (`bbo-1s`
and `bbo-1m`)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The end timestamp of the interval, clamped to the second/minute boundary, expressed as the number of nanoseconds since the UNIX epoch.  
`ts_event` | uint64_t | The matching-engine-received timestamp of the last trade for the instrument expressed as the number of nanoseconds since the UNIX epoch. Will be `UNDEF_TIMESTAMP` in cases where there was no last trade in the session. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Can be either 195 (`BBO-1s`) or 196 (`BBO-1m`). See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`side` | char | The side that initiated the last trade. Can be **A** sk for a sell aggressor in a trade, **B** id for a buy aggressor in a trade, or **N** one where no side is specified. See [Side](/docs/standards-and-conventions/common-fields-enums-types#side).  
`price` | int64_t | The last trade price where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. Will be `UNDEF_PRICE` if there was no last trade in the session. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`size` | uint32_t | The last trade quantity. Will be 0 if there was no last trade in the session.  
`flags` | uint8_t | A bit field indicating event end, message characteristics, and data quality. See [Flags](/docs/standards-and-conventions/common-fields-enums-types#flags).  
`sequence` | uint32_t | The message sequence number assigned at the venue of the last update.  
`bid_px_00` | int64_t | The bid price at the top level where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`ask_px_00` | int64_t | The ask price at the top level where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`bid_sz_00` | uint32_t | The bid size at the top level.  
`ask_sz_00` | uint32_t | The ask size at the top level.  
`bid_ct_00` | uint32_t | The bid order count at the top level.  
`ask_ct_00` | uint32_t | The ask order count at the top level.  
  
###### Consolidated
BBO on interval (CBBO)![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

**Consolidated BBO on interval (CBBO)** provides the consolidated last best
bid, best offer, and sale at 1-second or 1-minute intervals. This is a subset
of [CMBP-1](/docs/schemas-and-data-formats/mbp-1#consolidated-market-by-price-
cmbp-1).

Databento adopts the following convention for CBBO:

  * The time interval is indicated by the schema ID's suffix: `-1s` for 1-second and `-1m` for 1-minute.
  * If no trade or CBBO update occurs within the interval, no record is printed.
  * If a CBBO update occurs but no trade takes place within the interval, the last sale information is forward-filled from the previous interval.
  * If no CBBO update occurs but a trade takes place within the interval, the CBBO information is forward-filled from the previous interval.

## Fields
(`cbbo-1s` and `cbbo-1m`)![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The end timestamp of the interval, clamped to the second/minute boundary.  
`ts_event` | uint64_t | The matching-engine-received timestamp of the last trade for the instrument expressed as the number of nanoseconds since the UNIX epoch. Will be `UNDEF_TIMESTAMP` in cases where there was no last trade in the session. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Can be either 192 (`CBBO-1s`) or 193 (`CBBO-1m`). See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The consolidated publisher ID. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`side` | char | The side that initiated the last trade. Can be **A** sk for a sell aggressor in a trade, **B** id for a buy aggressor in a trade, or **N** one where no side is specified. See [Side](/docs/standards-and-conventions/common-fields-enums-types#side).  
`price` | int64_t | The last trade price where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. Will be `UNDEF_PRICE` if there was no last trade in the session. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`size` | uint32_t | The last trade quantity. Will be 0 if there was no last trade in the session.  
`flags` | uint8_t | A bit field indicating event end, message characteristics, and data quality. See [Flags](/docs/standards-and-conventions/common-fields-enums-types#flags).  
`bid_px_00` | int64_t | The bid price at the top level where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`ask_px_00` | int64_t | The ask price at the top level where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`bid_sz_00` | uint32_t | The bid size at the top level.  
`ask_sz_00` | uint32_t | The ask size at the top level.  
`bid_pb_00` | uint16_t | The publisher ID indicating the venue containing the best bid. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`ask_pb_00` | uint16_t | The publisher ID indicating the venue containing the best ask. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
  
## CBBO
publisher

The `publisher_id` field will always be the consolidated publisher ID for the
dataset.

`bid_pb_00` and `ask_pb_00` will represent the individual venues showing the
NBBO at the end of the interval.

## Implementation
differences between clients and encodings![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Bid and ask depth messages (fields starting with `bid_` and `ask_`) are
structured differently in the **C++ and Rust clients** , **the Python record
interface** , and **JSON data**. Instead of using the `_N` suffix, they're
stored in an array of structures named levels, with the top-of-book at index
`0`.

For example, in C++, `levels[5].bid_px` corresponds to `bid_px_05` in the
Python DataFrame API and CSV format.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> CBBO has many similarities to the CMBP-1 and TCBBO schemas. The main
> distinction is that CBBO is in time space, while CMBP-1 is in book update
> space, and TCBBO is in trade space. Learn about the differences between each
> in our [CMBP-1 vs. TCBBO vs. CBBO schemas guide](/docs/faqs/difference-
> between-mbp-and-tbbo).

