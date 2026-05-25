# ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Quickstart![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

## Set up
Databento

You need an API key to request data from Databento. [Sign
up](https://databento.com/signup) and you will automatically receive an API
key. Each API key is a 32-character string starting with `db-`, that can be
found from the [API keys](https://databento.com/portal/keys) page on your
portal.

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> Every new account receives [$125 in free data credits](/docs/faqs/usage-
> pricing-and-data-credits). These credits allow you to start receiving actual
> historical data immediately and verify your integration at no cost.

## Choose a
service

Databento provides two data services: historical and live. These two services
are nearly identical, but we separate them due to licensing fees and the
differences between response-request and real-time streaming APIs. You can
choose to integrate just one or both services.

| Historical | Live | Reference  
---|---|---|---  
Coverage | Historical and delayed intraday data within the exchange's live data restrictions. | Intraday data from within the last 24 hours and real-time data. | Historical with intraday updates.  
Pricing | Usage-based or flat-rate. No monthly license fees. | Flat-rate. Monthly license fees apply. | Dataset license fees apply.  
Access | Client libraries (C++, Python, and Rust) and API (HTTP). | Client libraries (C++, Python, and Rust) and API (Raw). | Client libraries (Python and Rust) and API (HTTP).  
  
## Build your first
application

Historical

Live

Reference

## Getting
historical data

1

Select how you will be integrating Databento below to see installation
instructions. By default, Python has been selected for you.

If you don't see an official client library for your preferred language, you
can still integrate our historical service through its HTTP API.

HISTORICAL DATA

Client libraries

![Python](/docs/assets/images/language/python-active.fe321c154b22733c4023.svg)

Python

![C++](/docs/assets/images/language/cpp.9e594c02365a931b1c9f.svg)

C++

![Rust](/docs/assets/images/language/rust.46089ddd614fe99ef504.svg)

Rust

APIs

![HTTP](/docs/assets/images/language/http.4b1578a08353533e397d.svg)

HTTP

![HTTP](/docs/assets/images/language/http.4b1578a08353533e397d.svg)

HTTP

$

pip install -U databento

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

![](/docs/assets/images/github-icon.b1cd95c91e2ff782811a.svg)

![](/docs/assets/images/star-outline.3fad7f44fa6a29496bb9.svg) 257

2

Install our Python client library with pip. Python 3.9+ is required.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    $ pip install databento
    

3

A simple Databento historical application looks like this.

This replays 10 minutes of trades of the entire CME Globex market event-by-
event.

Copy this to a file `main.py`. Then, run the file with `python main.py`.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("$YOUR_API_KEY")
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="ALL_SYMBOLS",
        start="2022-06-02T14:20:00",
        end="2022-06-02T14:30:00",
    )
    
    data.replay(print)
    

4

You can modify this application to specify particular
[instruments](/docs/faqs/instruments-and-products), and
[schemas](/docs/schemas-and-data-formats/whats-a-schema).

Let's get `ESM2` and `NQZ2` data in 1-second OHLCV bars.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("$YOUR_API_KEY")
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols=["ESM2", "NQZ2"],
        schema="ohlcv-1s",
        start="2022-06-06T14:30:00",
        end="2022-06-06T14:40:00",
    )
    
    data.replay(print)
    

You have successfully written your first historical data application with
Databento! Here are shortcuts to some of the next steps you can take:

  * To download a large amount of data to disk, see how to do a [batch download](/docs/faqs/streaming-vs-batch-download) of data files.
  * To get another dataset, just swap the dataset. You can get a list of datasets and their names from our [metadata](/docs/api-reference-historical/metadata).
  * You can use our [symbology](/docs/api-reference-historical/symbology) API to find other instrument IDs.
  * You can also find dataset names and instrument IDs interactively from our [search](https://databento.com).

To learn more, read the full documentation for our historical service under
[API reference - Historical](/docs/api-reference-historical).

## Getting live
data

1

Select how you will be integrating Databento below to see installation
instructions.

If you don't see an official client library for your preferred language, you
can still integrate our service via the Raw API.

LIVE DATA

Client libraries

![Python](/docs/assets/images/language/python-active.fe321c154b22733c4023.svg)

Python

![C++](/docs/assets/images/language/cpp.9e594c02365a931b1c9f.svg)

C++

![Rust](/docs/assets/images/language/rust.46089ddd614fe99ef504.svg)

Rust

APIs

![Raw](/docs/assets/images/language/binary.e211d063978f32ccbee4.svg)

Raw

![Raw](/docs/assets/images/language/binary.e211d063978f32ccbee4.svg)

Raw

$

pip install -U databento

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

![](/docs/assets/images/github-icon.b1cd95c91e2ff782811a.svg)

![](/docs/assets/images/star-outline.3fad7f44fa6a29496bb9.svg) 257

2

Install our Python client library with pip. Python 3.9+ is required.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    $ pip install -U databento
    

3

A simple Databento live application looks like this.

This prints 10 seconds of trades for all ES futures contracts event-by-event.

Copy this to a file `main.py`. Then, run the file with `python main.py`.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Live(key="$YOUR_API_KEY")
    
    client.subscribe(
        dataset="GLBX.MDP3",
        schema="trades",
        stype_in="parent",
        symbols="ES.FUT",
    )
    
    client.add_callback(print)
    
    client.start()
    
    client.block_for_close(timeout=10)
    

4

You can modify this application to specify particular
[instruments](/docs/faqs/instruments-and-products), and
[schemas](/docs/schemas-and-data-formats/whats-a-schema).

Let's get `ES.FUT` and `NQ.FUT` data in 1-second OHLCV bars.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Live(key="$YOUR_API_KEY")
    
    client.subscribe(
        dataset="GLBX.MDP3",
        schema="ohlcv-1s",
        stype_in="parent",
        symbols=["ES.FUT", "NQ.FUT"],
    )
    
    client.add_callback(print)
    
    client.start()
    
    client.block_for_close(timeout=10)
    

You have successfully written your first live data application with Databento!
To learn more, read the full documentation for our live service under [API
reference - Live](/docs/api-reference-live).

## Getting
reference data

1

Select how you will be integrating Databento below to see installation
instructions. By default, Python has been selected for you.

If you don't see an official client library for your preferred language, you
can still integrate our reference service through its HTTP API.

REFERENCE DATA

![](/docs/assets/images/help-outline.d1a258a8e3c35cac18c1.svg)

Client libraries

![Python](/docs/assets/images/language/python-active.fe321c154b22733c4023.svg)

Python

![Rust](/docs/assets/images/language/rust.46089ddd614fe99ef504.svg)

Rust

APIs

![HTTP](/docs/assets/images/language/http.4b1578a08353533e397d.svg)

HTTP

![HTTP](/docs/assets/images/language/http.4b1578a08353533e397d.svg)

HTTP

$

pip install -U databento

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

![](/docs/assets/images/github-icon.b1cd95c91e2ff782811a.svg)

![](/docs/assets/images/star-outline.3fad7f44fa6a29496bb9.svg) 257

2

Install our Python client library with pip. Python 3.8+ is required.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    $ pip install databento
    

3

A simple Databento reference application looks like this.

This request fetches all dividend event records for Microsoft from January
2019 until June 2024. You can modify this request to specify particular
[symbols](/docs/faqs/instruments-and-products), and [events](/docs/venues-and-
datasets/corporate-actions#events).

Copy this to a file `main.py`. Then, run the file with `python main.py`.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))[HTTP](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Reference("$YOUR_API_KEY")
    df = client.corporate_actions.get_range(
        start="2019",
        end="2024-07",
        symbols=["MSFT"],
        events=["DIV"],
        countries=["US"],
    )
    
    print(df)
    

You have successfully written your first reference data application with
Databento! Here are shortcuts to some of the next steps you can take:

  * Read the corporate actions [dataset guide](/docs/venues-and-datasets/corporate-actions).
  * Become familiar with the reference data [enum lookup tables](/docs/standards-and-conventions/reference-data-enums).
  * Go through the corporate actions [examples](/docs/examples/corporate-actions).

To learn more, read the full documentation for our reference service under
[Reference API](/docs/api-reference-reference).

## New user
guides

[What's a schema?See fields and columns included with each schema.![Supported
Schemas](/docs/02-06-01-supported-chemas.svg)](/docs/schemas-and-data-
formats/whats-a-schema)

[SymbologyHow to interpret instrument IDs, symbols, and
definitions.![Symbology](/docs/02-06-02-symbology.svg)](/docs/standards-and-
conventions/symbology)

[Normalization formatLearn more about our data normalization.![Normalization
Format](/docs/02-06-03-normalization-format.svg)](/docs/standards-and-
conventions/normalization)

[DBN files and encodingSave and read our data in DBN, our lightning-fast
binary format.![DBN Encoding](/docs/02-06-04-files-and-
encoding.svg)](/docs/standards-and-conventions/databento-binary-encoding)

[EquitiesGet started on equities data like Nasdaq TotalView-
ITCH.![Equities](/docs/02-06-05-equities.svg)](/docs/examples/equities/equities-
introduction)

[Equity optionsGet started on equity options data like OPRA.![Equity
Options](/docs/02-06-06-equity-options.svg)](/docs/examples/options/equity-
options-introduction)

[FuturesGet started on futures data like CME Globex MDP
3.0.![Futures](/docs/02-06-07-futures.svg)](/docs/examples/futures/futures-
introduction)

[Options on futuresGet started on options on futures data.![Futures
Options](/docs/02-06-08-futures-options.svg)](/docs/examples/options/options-
on-futures-introduction)

Data formats

[L3 • Full order book](/docs/examples/order-
book?historical=python&live=python&reference=python)[L2 • Market
depth](/docs/schemas-and-data-
formats/mbp-10?historical=python&live=python&reference=python)[L1 • Top of
book](/docs/schemas-and-data-
formats/mbp-1?historical=python&live=python&reference=python)[Trades](/docs/schemas-
and-data-
formats/trades?historical=python&live=python&reference=python)[Quotes](/docs/schemas-
and-data-
formats/bbo?historical=python&live=python&reference=python)[BBO](/docs/schemas-
and-data-
formats/bbo?historical=python&live=python&reference=python)[OHLCV](/docs/schemas-
and-data-
formats/ohlcv?historical=python&live=python&reference=python)[Statistics](/docs/schemas-
and-data-
formats/statistics?historical=python&live=python&reference=python)[Market
status](/docs/schemas-and-data-
formats/status?historical=python&live=python&reference=python)[Trading
halts](/docs/examples/basics-
historical/halts?historical=python&live=python&reference=python)[Trading
hours](/docs/examples/futures/trading-
hours?historical=python&live=python&reference=python)[Instrument
definitions](/docs/schemas-and-data-formats/instrument-
definitions?historical=python&live=python&reference=python)

Frequency

[Tick-by-tick](/docs/schemas-and-data-
formats/trades?historical=python&live=python&reference=python)[Second](/docs/schemas-
and-data-formats/ohlcv)[Minute](/docs/schemas-and-data-
formats/ohlcv)[Hourly](/docs/schemas-and-data-
formats/ohlcv)[Daily](/docs/schemas-and-data-formats/ohlcv)[End of
day](/docs/schemas-and-data-
formats/ohlcv?historical=python&live=python&reference=python)

Futures

[Continuous
contracts](/docs/examples/symbology/continuous?historical=cpp&live=raw&reference=http)[Front
month](/docs/examples/symbology/continuous?historical=python&live=python&reference=python)[Back
months](/docs/examples/symbology/parent-
symbology?historical=python&live=python&reference=python)[Open
interest](/docs/examples/futures/retrieving-oi-and-settlement-
prices?historical=python&live=python&reference=python)[Settlement
prices](/docs/examples/futures/retrieving-oi-and-settlement-
prices?historical=python&live=python&reference=python)[Cleared
volume](/docs/examples/futures/retrieving-oi-and-settlement-
prices?historical=python&live=python&reference=python)[Expirations](/docs/examples/futures/futures-
introduction/using-instrument-definitions-to-get-tick-size-expiration-and-
matching-algorithm?historical=python&live=python&reference=python)[Tick
size](https://databento.com/blog/tick-sizes-and-
values)[Spreads](/docs/examples/symbology/parent-
symbology?historical=python&live=python&reference=python)[User defined
instruments](/docs/examples/symbology/parent-
symbology?historical=python&live=python&reference=python)[Options on
futures](/docs/examples/options/options-with-underlying)

Options

[Stock options](/docs/examples/options/equity-options-introduction/opra)[ETF
options](/docs/examples/options/equity-options-introduction/opra)[Index
options](/docs/examples/options/zero-dte-options)[Option
chains](/docs/examples/options/equity-options-introduction/using-parent-
symbology-to-fetch-an-option-
chain?historical=python&live=python&reference=python)[Expirations](/docs/examples/options/equity-
options-introduction/using-instrument-definitions-to-get-symbols-strike-
prices-and-
expirations?historical=python&live=python&reference=python?utm_source=twitter)[Strikes](/docs/examples/options/equity-
options-introduction/using-instrument-definitions-to-get-symbols-strike-
prices-and-
expirations?historical=python&live=python&reference=python?utm_source=twitter)[0DTE](/docs/examples/options/zero-
dte-options)[Underlying prices](/docs/examples/options/options-and-
futures)[Open interest](/docs/examples/options/equity-open-
interest)[Volume](/docs/examples/options/equity-open-interest)[Implied
volatility](/docs/examples/options/estimating-implied-
volatility)[Greeks](https://databento.com/blog/option-greeks)

Equities

[Stocks](/docs/examples/equities/equities-introduction/finding-an-equities-
dataset)[ETFs](/docs/examples/equities/equities-introduction/finding-an-
equities-dataset)[NBBO](/docs/examples/equities/consolidated-
bbo?historical=python&live=python&reference=python)[Closing
prices](/docs/examples/equities/closing-
prices?historical=python&live=raw&reference=http)[Auction
imbalance](/docs/examples/equities/auction-
imbalance?historical=python&live=raw&reference=http)[Pre-
market](/docs/examples/equities/equities-premarket-
movers/)[Dividends](/docs/examples/corporate-
actions/dividends)[Splits](/docs/examples/corporate-actions/splits-and-
reverse-splits)

API features

[Flat files](/docs/portal/batch-
download?historical=python&live=python&reference=python)[DBN](/docs/examples/basics-
historical/encodings?historical=python&live=python&reference=python)[CSV](/docs/examples/basics-
historical/encodings?historical=python&live=python&reference=python)[JSON](/docs/examples/basics-
historical/encodings?historical=python&live=python&reference=python)[Parquet](/docs/examples/basics-
historical/encodings?historical=python&live=python&reference=python)[Write to
disk](/docs/examples/basics-
historical/encodings?historical=python&live=python&reference=python)[Read from
disk](/docs/examples/basics-
historical/encodings?historical=python&live=python&reference=python)[Snapshots](/docs/standards-
and-conventions/mbo-
snapshot?historical=python&live=python&reference=python)[Market
replay](/docs/api-reference-live/basics/intraday-
replay?historical=python&live=python&reference=python)[Timestamping](/docs/architecture/timestamping-
guide)[Performance optimization](/docs/architecture/performance-
optimization)[Latency](/docs/architecture/architecture-diagram#live-data-
architecture)[Cloud interconnects](/docs/architecture/dedicated-connectivity-
guide#interconnect-with-aws-google-cloud-or-microsoft-azure)[Cross-
connects](/docs/architecture/dedicated-connectivity-guide#cross-connect-with-
any-colocation-or-managed-services-provider-msp)[Raw PCAP
files](https://databento.com/blog/download-and-merge-pcaps-with-databento-for-
market-replay)

