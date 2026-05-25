Schemas and data formats

# Market by order
(MBO)

**Market by order (MBO)** provides every order book event across every price
level, keyed by its order ID. This allows you to determine the queue position
of each order and provides the highest level of granularity available.

MBO data includes all trades, fills, adds, cancels, modifies (or replaces),
book clear events, and, depending on the venue and dataset, other special
order events. It is often called "L3 data".

## Fields
(`mbo`)

Field | Type | Description  
---|---|---  
`ts_recv` | uint64_t | The capture-server-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_recv](/docs/standards-and-conventions/common-fields-enums-types#ts-recv).  
`ts_event` | uint64_t | The matching-engine-received timestamp expressed as the number of nanoseconds since the UNIX epoch. See [ts_event](/docs/standards-and-conventions/common-fields-enums-types#ts-event).  
`rtype` | uint8_t | A sentinel value indicating the record type. Always 160 in the MBO schema. See [Rtype](/docs/standards-and-conventions/common-fields-enums-types#rtype).  
`publisher_id` | uint16_t | The publisher ID assigned by Databento, which denotes the dataset and venue. See [Publishers](/docs/standards-and-conventions/common-fields-enums-types#publishers-datasets-and-venues).  
`instrument_id` | uint32_t | The numeric instrument ID. See [Instrument identifiers](/docs/standards-and-conventions/common-fields-enums-types#instrument-identifiers).  
`action` | char | The event action. Can be **A** dd, **C** ancel, **M** odify, clea**R** book, **T** rade, **F** ill, or **N** one. See [Action](/docs/standards-and-conventions/common-fields-enums-types#action).  
`side` | char | The side that initiates the event. Can be **A** sk for a sell order (or sell aggressor in a trade), **B** id for a buy order (or buy aggressor in a trade), or **N** one where no side is specified. See [Side](/docs/standards-and-conventions/common-fields-enums-types#side).  
`price` | int64_t | The order price where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001. See [Prices](/docs/standards-and-conventions/common-fields-enums-types#prices).  
`size` | uint32_t | The order quantity.  
`channel_id` | uint8_t | The channel ID assigned by Databento as an incrementing integer starting at zero.  
`order_id` | uint64_t | The order ID assigned by the venue.  
`flags` | uint8_t | A bit field indicating event end, message characteristics, and data quality. See [Flags](/docs/standards-and-conventions/common-fields-enums-types#flags).  
`ts_in_delta` | int32_t | The matching-engine-sending timestamp expressed as the number of nanoseconds before `ts_recv`. See [ts_in_delta](/docs/standards-and-conventions/common-fields-enums-types#ts-in-delta).  
`sequence` | uint32_t | The message sequence number assigned at the venue.  
  
## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Snapshots![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

For the convenience of managing state and recovery, Databento provides a
synthetic snapshot of the order book at the start of each UTC day in our
historical MBO data and periodic book snapshots in our real-time MBO data. The
mechanics of these snapshots is detailed [here](/docs/standards-and-
conventions/mbo-snapshot).

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> Learn more about the different action types and how to manage order state
> with respect to each action from our [State management of resting
> orders](/docs/examples/order-book/order-tracking) tutorial.
>
> Also learn how to construct a limit order book from MBO data from our [limit
> order book construction](/docs/examples/order-book/limit-order-book)
> tutorial.
>
> MBO data normalization differs slightly from one venue or dataset to
> another. Edge cases and differences are documented separately for each venue
> in the [Venues and datasets](/docs/venues-and-datasets) section.

