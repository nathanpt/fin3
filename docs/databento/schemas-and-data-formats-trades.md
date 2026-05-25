Schemas and data formats

# ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Trades![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

**Trades** provides every trade event. This is a subset of
[MBO](/docs/schemas-and-data-formats/mbo).

This is often referred to as "time and sales", "last sale," or "tick data."

## Fields
(`trades`)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The capture-server-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_recv](/docs/standards-and-conventions/common-fields-enums-types#ts-recv).  
`ts_event` | uint64_t | The matching-engine-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Always 0 in the trades schema. See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`action` | char | The event action. Always **T** rade in the trades schema. See [Action](/docs/standards-and-conventions/common-fields-enums-types#action).  
`side` | char | The side that initiates the trade. Can be **A** sk for a sell aggressor in a trade, **B** id for a buy aggressor in a trade, or **N** one where no side is specified. See [Side](/docs/standards-and-conventions/common-fields-enums-types#side).  
`depth` | uint8_t | The book level where the update event occurred.  
`price` | int64_t | The order price where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`size` | uint32_t | The order quantity.  
`flags` | uint8_t | A bit field indicating event end, message characteristics, and data quality. See [Flags](/docs/standards-and-conventions/common-fields-enums-types#flags).  
`ts_in_delta` | int32_t | The matching-engine-sending timestamp expressed as the number of nanoseconds before `ts_recv`. See [ts_in_delta](/docs/standards-and-conventions/common-fields-enums-types#ts-in-delta).  
`sequence` | uint32_t | The message sequence number assigned at the venue.

