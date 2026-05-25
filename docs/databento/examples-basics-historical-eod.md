Examples and tutorials

Historical data

# End-of-day
pricing and portfolio valuation![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

This example shows how to use the [Historical client](/docs/api-reference-
historical/client) to calculate the end-of-day (EOD) value of a hypothetical
portfolio. To do this we will request the closing price for each symbol on a
given date and use this price to determine the value of our portfolio.

## OHLCV-1d
schema

We'll demonstrate this example using the [OHLCV-1d schema](/docs/schemas-and-
data-formats/ohlcv). The OHLCV family of schemas contain the opening, high,
low and closing prices as well as the aggregated volume of trades within a
time interval. Since we are interested in end-of-day evaluation, we'll use an
interval of one day, which is specified by the suffix `-1d`.

Many users will prefer to use the official daily settlement prices found in
the `statistics` schema for this purpose.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Example![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    # A hypothetical portfolio mapping symbols to a quantity of shares
    portfolio = {
        "AAPL": 200,
        "AMZN": 200,
        "GOOG": 100,
        "META": 100,
        "NFLX": 114,
    }
    
    # Create a historical client
    client = db.Historical("$YOUR_API_KEY")
    
    # Request OHLCV-1d data
    data = client.timeseries.get_range(
        dataset="XNAS.ITCH",
        start="2022-09-19",
        symbols=list(portfolio.keys()),
        stype_in="raw_symbol",
        schema="ohlcv-1d",
    )
    
    # Convert to DataFrame
    eod_data = data.to_df()
    eod_data = eod_data.set_index("symbol")
    
    # Sum the products of the close prices and portfolio quantities
    eod_evaluation = sum(
        eod_data.at[symbol, "close"] * quantity for symbol, quantity in portfolio.items()
    )
    print(f"${eod_evaluation:,.2f}")
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Result![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    $109,235.90
    

