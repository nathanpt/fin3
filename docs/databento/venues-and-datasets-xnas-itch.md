Venues and datasets

# Nasdaq TotalView-
ITCH

**Dataset ID** : [XNAS.ITCH](https://databento.com/catalog/us-
equities#XNAS.ITCH)

Nasdaq disseminates full depth-of-book data for their exchange through their
TotalView-ITCH feed over the MoldUDP64 protocol, which Databento receives over
UDP multicast in our NY4 data center.

PDF specifications are available on Nasdaq's website for [Nasdaq TotalView-
ITCH](http://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf)
and
[MoldUDP64](http://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf).

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Timestamps![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Nasdaq TotalView-ITCH messages received by Databento have at most one
timestamp, which has nanosecond precision. As with all of our datasets, we
also collect a `ts_recv` timestamp when the packet was received by our capture
server.

The extra field `ts_in_delta` does not convey any additional information for
this dataset, and is equivalent to `ts_recv - ts_event`.

Databento Field | TotalView Field | Description  
---|---|---  
`ts_recv` | N/A | The capture-server-received timestamp.  
`ts_event` | Timestamp | The matching-engine-received timestamp.  
  
More details about our timestamps are available in our [timestamping
guide](/docs/architecture/timestamping-guide).

## MBO
normalization

**Order lifecycle messages**

There are seven message types in TotalView that change the state of the order
book. Databento normalizes these messages to MBO records as follows:

  * _Add Order - No MPID Attribution_ : **A** dd
  * _Add Order with MPID Attribution_ : **A** dd, but the attribution field is not included
  * _Order Executed_ : **T** rade, **F** ill, then **C** ancel
  * _Order Executed with Price_ : **T** rade, **F** ill, then **C** ancel, but the initial **T** rade is omitted if the message is marked as non-printable
  * _Order Cancel_ : **C** ancel, with the `quantity` indicating the number of shares to remove from the display size
  * _Order Delete_ : **C** ancel, with the `quantity` indicating the current display size of the order
  * _Order Replace_ : **C** ancel with the previous `order_id`, followed by **A** dd with the new `order_id`

All **T** rade records will have `order_id` set to `0`, because Nasdaq does
not provide information about the aggressor's order. Normalized records from
the same TotalView message will have the same `sequence`.

**Trade messages**

TotalView also has two messages for providing execution details for match
events against non-displayed orders or cross events.

Databento normalizes both _Trade (Non-Cross)_ and _Cross Trade_ messages is
into a single MBO **T** rade record with side **N** one.

Nasdaq will always send a _Cross Trade_ message following an opening, closing,
or EMC cross event for an instrument. If no shares are matched during a cross
event, `quantity` will be `0` and `price` will be `UNDEF_PRICE`
(`0x7fffffffffffffff`, `9223372036854775808`).

## Statistics
normalization

TotalView provides information about the execution price and shares matched on
completion of a cross event in their _Cross Trade_ message. Databento
normalizes each message into a record in the [statistics
schema](/docs/schemas-and-data-formats/statistics).

Statistics are categorized by the following cross events:

  * Opening price: Nasdaq Opening Cross
  * Closing price: Nasdaq Closing Cross
  * Uncrossing price: EMC Cross (Halt/IPO)

If no shares are matched during a cross event, `quantity` will be `0` and
`price` will be `UNDEF_PRICE` (`0x7fffffffffffffff`, `9223372036854775808`).

## Imbalance
normalization

TotalView provides information about cross imbalances in their _Net Order
Imbalance Indicator (NOII)_ message. Databento normalizes each message into a
record in the [imbalance schema](/docs/schemas-and-data-formats/imbalance).

Databento Field | TotalView Field | Description  
---|---|---  
`paired_qty` | Paired Shares | The number of shares that are eligible to match at the `ref_price`.  
`total_imbalance_qty` | Imbalance Shares | The number of shares not paired at the `ref_price`.  
`side` | Imbalance Direction | The market side of the order imbalance: **B** id (buy) imbalance, **A** sk (sell) imbalance, **N** o imbalance.  
`auct_interest_clr_price` | Far Price | Hypothetical auction-clearing price for cross orders only.  
`cont_book_clr_price` | Near Price | Hypothetical auction-clearing price for cross and continuous orders.  
`ref_price` | Current Reference Price | The price at which imbalance shares are being calculated.  
`auction_type` | Cross Type | The type of cross: **O** pening, **C** losing, un-**H** alt or IPO, **A** : Extended Trading Close.  
`significant_imbalance` | Price Variation Indicator | Indicates the deviation of `cont_book_clr_price` to the `ref_price`. Refer to the Nasdaq TotalView specification for values.  
  
##### **Opening/Closing Cross**

Nasdaq begins disseminating imbalance data at 9:25 ET for the Opening Cross
and 15:50 ET for the Closing Cross. Between 9:25 and 9:28, and 15:50 and
15:55, imbalance data is sent every 10 seconds. The `auct_interest_clr_price`
and `cont_book_clr_price` fields will not be populated during these times.
After 9:28 and 15:55, imbalance data is sent every 1 second and will include
`auct_interest_clr_price` and `cont_book_clr_price` data.

At the conclusion of the Opening Cross at 9:30 and the Closing Cross at 16:00,
a [statistics record](/docs/venues-and-datasets/xnas-itch#statistics-
normalization) will be published with the official opening/closing price.

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> For more information on the Nasdaq Opening and Closing Cross, see this
> [Nasdaq
> FAQ](https://nasdaqtrader.com/content/productsservices/trading/crosses/openclose_faqs.pdf).

## Definition
normalization

Definition records are sourced from Stock Directory messages.

Databento Field | TotalView Field | Description  
---|---|---  
`exchange` | Market Category | Normalized to the MIC code of the listing venue. All Nasdaq-listed instruments will normalize to `XNAS`.  
`security_type` | Issue Classification | Identifies the security class for the issue as assigned by Nasdaq. A list of the different values and their meanings can be found in Appendix D [here](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf).  
`secsubtype` | Issue Sub-Type | Identifies the security sub-type for the issue as assigned by Nasdaq. A list of the different values and their meanings can be found in Appendix E [here](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf).

