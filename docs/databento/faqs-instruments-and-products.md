FAQs

# Instruments and
products

Our documentation and web platform uses the terms **product** , **instrument**
, and **listing** for different purposes. This article clarifies the
differences between these terms.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Products![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

On Databento, a **product** (also called a parent product or parent in the
context of futures or options) refers to any real or synthetic asset whose
data is provided by a market. A product describes a group of instruments
belonging to a given economic sector or market segment. Also, products are
often _fungible_ , meaning they may be traded on more than one venue. In the
case of equities, this means that products and instruments are the same asset,
in contrast to derivatives such as futures and options products.

The search on our [home page](https://databento.com) looks up products across
our dataset coverage. To find individual instruments, click on any of the
products in the search results.

A futures product for a certain underlying has a range of expirations. To
avoid ambiguity, we will refer to the collection of all expirations as a
**parent** , and specific expiration as an **instrument**. So, for example,
`ES.FUT` is the parent of `ESM0` and `ESZ0`. Note: we classify exchange-traded
spreads between futures outrights as part of the futures products.

An options product for a certain underlying has a range of both expirations
and strikes. To avoid ambiguity, we will refer to the collection of all
expirations and strikes as a **parent** , and specific expiration with strike
as an **instrument**. So, for example, `MSFT.OPT` is the parent of
`MSFT20210205C210`. Note: we classify option combinations as part of the
options products.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Instruments![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

On Databento, an **instrument** (also called a child instrument or child) is a
tradable asset, real or synthetic, on a specific market. Instruments define
all attributes of what is traded, e.g. product complex, product group,
expiration, and strike price.

Some publishers use the term **security** in lieu of what we consider an
instrument. This becomes somewhat of a misnomer when applied to derivatives,
which are not strictly securities. As such, we prefer to use the term
**instrument** as it more broadly includes both securities and derivatives.

Moreover, some markets provide multiple datasets which provide different
levels of visibility. Two different datasets from the same market may exhibit
different data for the same instrument. For instance, FX ECNs will often
provide a premium feed with full visibility of real-time trades across all
liquidity pools on the ECN as well as an entry feed with only delayed trade
prints for a subset of liquidity. Since we support multiple datasets from the
same market, we also need to distinguish if you're requesting data for EUR/USD
on the premium feed or the entry feed.

To resolve these identification issues, we use the term **listing** to refer
to a tradeable entity in a specific dataset from a specific publisher. As
such, we consider AAPL on NASDAQ TotalView-ITCH 5.0, AAPL on NYSE OpenBook
Ultra, and AAPL on NYSE Trades as three distinct _listings_. The following
table provides more of such examples.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Listings![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

On Databento, a listing is an instrument specific to a certain venue, which is
not transferable or representable across other venues (_non-fungible_). While
this makes a listing synonymous with an instrument for equities, it's
important to be mindful of the distinction if you intend on extending into
other asset classes.

| Equities | FX | Futures | Options  
---|---|---|---|---  
Product | `AAPL`, `MSFT` | `EUR/USD` | `ES.FUT`, `GE.FUT` | `MSFT.OPT`, `ES.OPT`, `EW1.OPT`  
Instrument | `AAPL`, `MSFT` | `EUR/USD` _spot_ | `ESM0`, `ESZ0` | `MSFT20210205C210`,`ESZ0 C3620`,`ESZ0 P3620`,`EW1Z0 C3500`  
Listing | `AAPL` _on Nasdaq_ , `AAPL ` _on NYSE Arca_ | `EUR/USD` _on Cboe SEF_ | `ESM0` _on CME Globex_ | `MSFT20210205C210` _on BOX_ , `ESZ0 C3620` _on CME Globex_

