Schemas and data formats

# Market by price
(MBP-10)

**MBP-10 (market by price)** provides every order book event across the top
ten price levels, keyed by price. This includes every trade and changes to
aggregate market depth, alongside total size and order count at the top ten
price levels.

This is often called "L2 data".

## Fields
(`mbp-10`)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The capture-server-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_recv](/docs/standards-and-conventions/common-fields-enums-types#ts-recv).  
`ts_event` | uint64_t | The matching-engine-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Always 10 in the MBP-10 schema. See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`action` | char | The event action. Can be **A** dd, **C** ancel, **M** odify, clea**R** book, or **T** rade. See [Action](/docs/standards-and-conventions/common-fields-enums-types#action).  
`side` | char | The side that initiates the event. Can be **A** sk for a sell order (or sell aggressor in a trade), **B** id for a buy order (or buy aggressor in a trade), or **N** one where no side is specified. See [Side](/docs/standards-and-conventions/common-fields-enums-types#side).  
`depth` | uint8_t | The book level where the update event occurred.  
`price` | int64_t | The order price where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`size` | uint32_t | The order quantity.  
`flags` | uint8_t | A bit field indicating event end, message characteristics, and data quality. See [Flags](/docs/standards-and-conventions/common-fields-enums-types#flags).  
`ts_in_delta` | int32_t | The matching-engine-sending timestamp expressed as the number of nanoseconds before `ts_recv`. See [ts_in_delta](/docs/standards-and-conventions/common-fields-enums-types#ts-in-delta).  
`sequence` | uint32_t | The message sequence number assigned at the venue.  
`bid_px_N` | int64_t | The bid price at level N (top level if N = 00) where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`ask_px_N` | int64_t | The ask price at level N (top level if N = 00) where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`bid_sz_N` | uint32_t | The bid size at level N (top level if N = 00).  
`ask_sz_N` | uint32_t | The ask size at level N (top level if N = 00).  
`bid_ct_N` | uint32_t | The bid order count at level N (top level if N = 00).  
`ask_ct_N` | uint32_t | The ask order count at level N (top level if N = 00).  
  
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
> It is possible to construct MBP-10 yourself from MBO data if you want more
> price levels or prefer to reduce your bandwidth use. Learn how to construct
> a limit order book from MBO data from our [limit order book
> construction](/docs/examples/order-book/limit-order-book) tutorial.

