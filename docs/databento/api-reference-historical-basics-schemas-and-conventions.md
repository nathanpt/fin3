### Schemas and
conventions

A schema is a data record format represented as a collection of different data
fields. Our datasets support multiple schemas, such as order book, trades, bar
aggregates, and so on. You can get a dictionary describing the fields of each
schema from our [List of market data schemas](/docs/schemas-and-data-
formats/whats-a-schema).

You can get a list of all supported schemas for any given dataset using the
Historical client's [list_schemas](/docs/api-reference-
historical/metadata/metadata-list-schemas) method. The same information can
also be found on the dataset details pages on the [user
portal](https://databento.com).

The following table provides details about the data types and conventions used
for various fields that you will commonly encounter in the data.

Name | Field | Description  
---|---|---  
Dataset | `dataset` | A unique string name assigned to each dataset by Databento. Full list of datasets can be found from the [metadata](/docs/api-reference-historical/metadata/metadata-list-datasets).  
Publisher ID | `publisher_id` | A unique 16-bit unsigned integer assigned to each publisher by Databento. Full list of publisher IDs can be found from the [metadata](/docs/api-reference-historical/metadata/metadata-list-publishers).  
Instrument ID | `instrument_id` | A unique 32-bit unsigned integer assigned to each instrument by the venue. Information about instrument IDs for any given dataset can be found in the [symbology](/docs/api-reference-historical/symbology).  
Order ID | `order_id` | A unique 64-bit unsigned integer assigned to each order by the venue.  
Timestamp (event) | `ts_event` | The matching-engine-received timestamp expressed as the number of nanoseconds since the [UNIX epoch](https://en.wikipedia.org/wiki/Unix_time).  
Timestamp (receive) | `ts_recv` | The capture-server-received timestamp expressed as the number of nanoseconds since the [UNIX epoch](https://en.wikipedia.org/wiki/Unix_time).  
Timestamp delta (in) | `ts_in_delta` | The matching-engine-sending timestamp expressed as the number of nanoseconds before `ts_recv`. See [timestamping](/docs/architecture/timestamping-guide) guide.  
Timestamp out | `ts_out` | The Databento gateway-sending timestamp expressed as the number of nanoseconds since the [UNIX epoch](https://en.wikipedia.org/wiki/Unix_time). See [timestamping](/docs/architecture/timestamping-guide) guide.  
Price | `price` | The price expressed as signed integer where every 1 unit corresponds to 1e-9, i.e. 1/1,000,000,000 or 0.000000001.  
Book side | `side` | The side that initiates the event. Can be **A** sk for a sell order (or sell aggressor in a trade), **B** id for a buy order (or buy aggressor in a trade), or **N** one where no side is specified by the original source.  
Size | `size` | The order quantity.  
Flag | `flag` | A bit field indicating event end, message characteristics, and data quality.  
Action | `action` | The event type or order book operation. Can be **A** dd, **C** ancel, **M** odify, clea**R** book, **T** rade, **F** ill, or **N** one.  
Sequence number | `sequence` | The original message sequence number from the venue.

