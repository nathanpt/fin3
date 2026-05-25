Examples and tutorials

Symbology

# Continuous
contracts

> ![Info](/docs/assets/images/callout-info.3bc1b52b03055eb8f20b.svg)
>
> Info
>
> Our continuous contract symbology is a notation that maps to an actual,
> tradable instrument on any given date. The continuous contract prices
> provided are the original, unadjusted prices. Unlike some vendor
> implementations that back-adjust prices to remove jumps during rollovers,
> our approach maintains the original properties of the data.

When dealing with far-reaching historical data on expiring products (like the
[E-mini S&P 500
Futures](https://databento.com/portal/datasets/GLBX.MDP3/Futures/ES)), users
have to be concerned with expiration cycles and what is defined as the [front
month](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/CFTCGlossary/index.htm#frontmonth)
or first expiry, second expiry, etc.

When requesting (E-mini S&P 500 Futures) data, we can specify the particular
product we want. Take for example, `ESH5`, the March 2025 expiring E-Mini S&P
500 futures contract. Since this instrument has a March 2025 expiry, it will
not exist after March 2025. Since March 2025 is the front month (i.e. the
nearest expiration date for the contract) only from December 2024 to March
2025, the instrument's data dating before December 2024 could be considered
irrelevant.

Continuous contracts symbology provides a way to navigate this complexity. You
can easily access recent contracts: front month, second expiry, and so on.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Symbology![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

To access a specific contract in a continuous contract product, query for that
contract using this symbol structure.

`[ticker].v.[expiry]`

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Expiry![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In place of the exchange's proprietary code for specifying the month of
expiration (e.g. `H5` for `ESH5` in CME data), we use an index to refer to
which expiry in the current cycle we want.

Index | Expiry  
---|---  
0 | front month  
1 | second expiry  
2 | third expiry  
n | nth expiry  
  
## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Algorithm![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

To request the front month for the `ES` product based on volume, use `ES.v.0`.
In this example, the v indicates the calendar roll rule. The zero corresponds
to the front month. Learn more about continuous contract symbology in our
[continuous symbology](/docs/standards-and-conventions/symbology#continuous)
guide.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Example![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    # Set parameters
    dataset = "GLBX.MDP3"
    product = "ES"
    start = "2025-03-16"
    end = "2025-03-23"
    
    # Create a historical client
    client = db.Historical("$YOUR_API_KEY")
    
    # Request OHLCV-1d data for the continuous contract
    data = client.timeseries.get_range(
        dataset=dataset,
        schema="ohlcv-1d",
        symbols=f"{product}.v.0",
        stype_in="continuous",
        start=start,
        end=end,
    )
    
    # Convert to DataFrame
    df = data.to_df()
    
    print(df)
    

The data received is now related to the volume-based front month for each day
of the requested time range. If we are interested on the second month then we
can just use `ES.v.1` instead.

The results below are from the week of expiration for `ES`. As you can see,
the contract that maps to the volume-based front month, as shown by the
`instrument_id` field, changes mid-week as the volume shifts to the new
contract.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                               rtype  publisher_id  instrument_id     open     high      low    close   volume  symbol
    ts_event
    2025-03-16 00:00:00+00:00     35             1           5002  5626.75  5632.00  5602.50  5608.50    30078  ES.v.0
    2025-03-17 00:00:00+00:00     35             1           5002  5608.75  5707.50  5599.00  5679.50   723022  ES.v.0
    2025-03-18 00:00:00+00:00     35             1           5002  5679.75  5683.25  5600.00  5621.25   412431  ES.v.0
    2025-03-19 00:00:00+00:00     35             1           4916  5672.50  5770.50  5657.50  5747.75  1331926  ES.v.0
    2025-03-20 00:00:00+00:00     35             1           4916  5747.50  5765.25  5682.50  5714.75  1503542  ES.v.0
    2025-03-21 00:00:00+00:00     35             1           4916  5714.25  5723.75  5651.25  5720.00  1454908  ES.v.0
    

