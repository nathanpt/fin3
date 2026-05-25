Examples and tutorials

Historical data

# Request a large
number of symbols

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> If you’re new to Databento, see the [Quickstart guide](/docs/quickstart) for
> a walkthrough on setting up our live and historical APIs.

Databento supports several ways for you to get multiple symbols in a single
request.

If you're interested in a large universe of symbols, we recommend you use any
of these supported approaches and filter the symbols on client side as opposed
to requesting one symbol at a time. This minimizes the number of requests,
which will usually give you better performance.

## Getting all
symbols with `"ALL_SYMBOLS"`![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

The simplest way to do this is to get all symbols of a given dataset. Passing
in `"ALL_SYMBOLS"` as the `symbols` argument will give you every listed
symbol.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("$YOUR_API_KEY")
    
    data = client.timeseries.get_range(
        dataset="XNAS.ITCH",
        schema="trades",
        symbols="ALL_SYMBOLS",
        start="2024-05-21T15:00",
        end="2024-05-21T15:01",
    )
    
    df = data.to_df()
    
    print(f"{len(df):,d} trade(s) for {len(df['instrument_id'].unique()):,d} instrument(s)")
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    31,405 trade(s) for 2,854 instrument(s)
    

This works for live subscriptions as well, which gives you a firehose
subscription to all symbols:

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Live("$YOUR_API_KEY")
    
    client.subscribe(
        dataset="XNAS.ITCH",
        schema="ohlcv-1s",
        symbols="ALL_SYMBOLS",
    )
    

Our live data gateways are capable of delivering every book event on most
venues—like a direct feed, except over internet. You'll usually run into
bandwidth limitations long before our gateway runs into other practical
limits. If your application requires real-time data of all symbols at the
granularity of the `trades` schema or more, we recommend you to look into our
supported options for [dedicated connectivity](/docs/architecture/dedicated-
connectivity-guide).

## Getting
multiple, specific symbols![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

If you're only interested in a subset of specific symbols, you can pass in a
list of symbols—this works for all symbology types. We support a maximum of
2,000 symbols in a single request.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("$YOUR_API_KEY")
    
    data = client.timeseries.get_range(
        dataset="XNAS.ITCH",
        schema="trades",
        symbols=["AAPL", "MSFT", "NVDA"],
        start="2024-05-21T15:00",
        end="2024-05-21T15:01",
    )
    

Likewise, this works for live subscriptions as well:

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Live("$YOUR_API_KEY")
    
    client.subscribe(
        dataset="XNAS.ITCH",
        schema="ohlcv-1s",
        symbols=["AAPL", "MSFT", "NVDA"],
    )
    

If you need more than 2,000 symbols in a single request, you can split your
symbol list and dispatch multiple requests, but in such situations we usually
encourage you to fetch all symbols and discard unwanted symbols on client
side, even if it may cost more.

There's a practical reason for this recommendation and why we limit to the
number of symbols you can specify in this manner. While it may seem
unintuitive, but selecting specific symbols is usually a much slower operation
than requesting all symbols, because our servers have to validate your symbol
list, resolve any parent symbols into their child instruments, and inspect
each event if it qualifies your selection.

## Getting all
child instruments using parent symbology![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

For futures and options data, there's often a need to get all symbols with the
same underlying or all symbols that share the same product root. We allow you
to reference such a product or cluster of symbols with the same underlying
using a **parent symbol**.

To specify a parent symbol for futures, you can pass in `"parent"` as the
input symbology argument, `stype_in`. You must append the `.FUT` suffix to the
symbol:

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> For more on parent symbology, see the [Symbology](/docs/standards-and-
> conventions/symbology#parent) guide.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("$YOUR_API_KEY")
    
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        schema="trades",
        stype_in="parent",
        symbols=["ES.FUT", "NQ.FUT"],
        start="2024-05-21T15:00",
        end="2024-05-21T15:01",
    )
    
    df = data.to_df()
    
    print(f"{len(df):,d} trade(s) for {len(df['instrument_id'].unique()):,d} instrument(s)")
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    1,101 trade(s) for 4 instrument(s)
    

We can also use parent symbology for options with the `.OPT` suffix:

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("$YOUR_API_KEY")
    
    data = client.timeseries.get_range(
        dataset="OPRA.PILLAR",
        schema="trades",
        stype_in="parent",
        symbols=["QQQ.OPT", "VIX.OPT"],
        start="2024-05-21T15:00",
        end="2024-05-21T15:01",
    )
    
    df = data.to_df()
    
    print(f"{len(df):,d} trade(s) for {len(df['instrument_id'].unique()):,d} instrument(s)")
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    1,021 trade(s) for 146 instrument(s)
    

Common use cases of parent symbology include fetching [option
chains](/docs/examples/options/equity-options-introduction/using-parent-
symbology-to-fetch-an-option-chain), all outrights and expirations of a
futures product, or all maturities of a fixed income product.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> See the [0DTE symbols](/docs/examples/options/zero-dte-options) example on
> how to filter instruments based on `expiration`.

