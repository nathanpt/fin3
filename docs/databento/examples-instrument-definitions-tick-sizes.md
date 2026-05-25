Examples and tutorials

Instrument definitions

# Handling tick
sizes

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In this example we will use the [Historical client](/docs/api-reference-
historical/client/historical) to retrieve instrument definitions and top-of-
book data for two futures contracts during non-US equity cash session hours.
We will then calculate the mean spread and normalize the values to a discrete
number of price increments or "ticks" using the `min_price_increment` field
from the definition schema. Finally, we will plot the changes in mean spread.

The futures we will use for this example are [British Pound Futures
(6B)](https://databento.com/portal/datasets/GLBX.MDP3/Futures/6B) and [Euro FX
Futures (6E)](https://databento.com/portal/datasets/GLBX.MDP3/Futures/6E). We
will use [continuous contract symbology](/docs/standards-and-
conventions/symbology#continuous) to request the second highest contracts by
open interest, which should have a more dynamic mean spread.

## Definition
schema

The [definition schema](/docs/schemas-and-data-formats/instrument-definitions)
contains instrument definitions and properties. In this example, we are
interested in the `min_price_increment` field, which will allow us to
normalize a spread to a discrete number of increments.

## Mean
spread

The mean spread is the average difference between the bid and ask price of an
instrument. The mean spread is a metric which helps traders understand the
liquidity and transaction costs associated with a particular trade. Illiquid
and volatile instruments tend to have a larger mean spread. Such instruments
are more likely to experience price slippage, where the actual price of a
trade moves or "slips" away from the expected price.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Dependencies![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

For plotting, this example will use the
[matplotlib](https://pypi.org/project/matplotlib/) package.

This dependency can be installed with the following:

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    $ pip install matplotlib
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Example![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    import pandas as pd
    from matplotlib import pyplot as plt
    
    # Get symbols for 6B and 6E futures with the second highest open interest
    symbols = ["6B.n.1", "6E.n.1"]
    
    # Define a start and end time for this example
    dataset = "GLBX.MDP3"
    start = pd.Timestamp("2024-04-07T21:00:00", tz="US/Eastern")
    end = pd.Timestamp("2024-04-08T04:00:00", tz="US/Eastern")
    
    # First, create a historical client
    client = db.Historical(key="$YOUR_API_KEY")
    
    # Next, retrieve the instrument definitions for our symbols
    definitions_data = client.timeseries.get_range(
        dataset=dataset,
        schema="definition",
        symbols=symbols,
        stype_in="continuous",
        start=start.date(),
    )
    
    # And extract the min_price_increment field
    definitions_df = definitions_data.to_df()
    tick_sizes = definitions_df[
        ["raw_symbol", "instrument_id", "min_price_increment"]
    ].set_index(
        "instrument_id",
    )
    
    # Print the min_price_increment for each symbol
    print(tick_sizes)
    
    # Then, request top-of-book updates for our symbols overnight
    mbp_data = client.timeseries.get_range(
        dataset=dataset,
        schema="MBP-1",
        symbols=symbols,
        stype_in="continuous",
        start=start,
        end=end,
    )
    
    # And convert to a DataFrame and join with our tick size
    mbp_df = mbp_data.to_df(tz="US/Eastern")
    mbp_df = mbp_df.join(tick_sizes, on="instrument_id")
    mbp_df = mbp_df[["raw_symbol", "ask_px_00", "bid_px_00", "min_price_increment"]]
    
    # Now, calculate the spread in number of ticks
    mbp_df["spread"] = (mbp_df["ask_px_00"] - mbp_df["bid_px_00"]) / mbp_df[
        "min_price_increment"
    ]
    
    # Finally, plot the mean spread for each symbol
    for symbol, data in mbp_df.groupby("raw_symbol"):
        # Average this data using a 5-minute rolling window
        mean_spread = (
            data["spread"]
            .ewm(
                times=data.index,
                halflife=pd.Timedelta(minutes=5),
            )
            .mean()
            .reset_index()
        )
    
        plt.plot(
            "ts_recv",
            "spread",
            data=mean_spread,
            label=symbol,
        )
    
    plt.legend()
    plt.title(f"Overnight Mean Spread - {start.date()}")
    plt.ylabel("Mean Spread (ticks)")
    plt.xlabel("Time (UTC)")
    plt.show()
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Result![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                  raw_symbol  min_price_increment
    instrument_id
    200251              6BU4              0.00010
    201330              6EU4              0.00005
    

![Handling tick sizes](/docs/05-07-04-tick-sizes-0.svg)

