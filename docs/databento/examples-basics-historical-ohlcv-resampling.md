Examples and tutorials

Historical data

# Resample OHLCV
from 1-minute to 5-minute![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Databento follows a convention in the [OHLCV schema](/docs/schemas-and-data-
formats/ohlcv) to only publish a record when a trade occurs in the interval.

This approach is adopted by most data vendors for two key reasons:

  * Multiple interpolation strategies exist and the optimal choice depends on the specific use case. Client-side interpolation keeps the strategy transparent.
  * This reduces storage and bandwidth requirements, especially for markets with many illiquid instruments like options.

This example demonstrates one way to interpolate OHLCV-1m data to ensure
exactly one row per minute. The interpolation strategy used in this example
does the following:

  * Forward-fills the close price from the last known value.
  * Sets open, high, and low equal to the forward-filled close price.
  * Sets volume to 0 for interpolated periods.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In this example, we'll use the [historical client](/docs/api-reference-
historical/client) to request data from the [OHLCV-1m schema](/docs/schemas-
and-data-formats/ohlcv). First, we'll highlight how to interpolate missing
rows in the 1-minute data. Next, we'll show how to resample the data to
5-minute bars.

Note that while this example is showing these methods being used individually,
you can also chain these methods together.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Example![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    import pandas as pd
    
    def interpolate_ohlcv(
        df: pd.DataFrame,
        start: pd.Timestamp,
        end: pd.Timestamp,
        interp_interval: str,
    ) -> pd.DataFrame:
        """
        Interpolate OHLCV records between `start` and `end`, since Databento only sends
        an OHLCV record if a trade happens in that interval.
        """
        def _interpolate_group(group):
            """Interpolate OHLCV records for each group"""
            # Reindex with a complete index using specified start/end times
            group = group.reindex(
                pd.date_range(
                    start=start,
                    end=end,
                    freq=interp_interval,
                    inclusive="left",
                ).rename(group.index.name),
            )
            # Forward fill close prices (may remain NaN if no prior data exists)
            group["close"] = group["close"].ffill()
    
            # For intervals with no trades, set open/high/low equal to the close and volume to 0
            group = group.fillna({
                **{col: group["close"] for col in ["open", "high", "low"]},
                "volume": 0,
            })
            group["volume"] = group["volume"].astype(int)
            group = group.drop(columns=["rtype", "instrument_id"], errors="ignore")
    
            return group
    
        df = (
            df.groupby(["publisher_id", "symbol"])
            .apply(_interpolate_group, include_groups=False)
            .reset_index(["publisher_id", "symbol"])
            .sort_values(["ts_event", "publisher_id", "symbol"])
        )
        return df
    
    
    def resample_ohlcv(
        df: pd.DataFrame,
        resample_interval: str,
    ) -> pd.DataFrame:
        """Resample OHLCV bars to the specified interval"""
        resampled_df = (
            df.groupby(["publisher_id", "symbol"])
            .resample(resample_interval)
            .agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            })
            .reset_index(["publisher_id", "symbol"])
            .sort_values(["ts_event", "publisher_id", "symbol"])
        )
    
        return resampled_df
    
    
    # Set parameters
    start = pd.Timestamp("2024-12-17T09:30:00", tz="US/Eastern")
    end = pd.Timestamp("2024-12-17T16:00:00", tz="US/Eastern")
    
    # Create a historical client
    client = db.Historical("$YOUR_API_KEY")
    
    # Request OHLCV-1m data for all AAPL options and convert to DataFrame
    df = client.timeseries.get_range(
        dataset="OPRA.PILLAR",
        schema="ohlcv-1m",
        symbols="AAPL.OPT",
        stype_in="parent",
        start=start,
        end=end,
    ).to_df(tz=start.tzinfo)
    
    # Interpolate missing rows.
    df1 = interpolate_ohlcv(df, start, end, "1min")
    print(df1)
    
    # Resample to 5-minute bars
    df2 = resample_ohlcv(df, "5min")
    print(df2)
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Result![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                               publisher_id                 symbol   open   high    low  close  volume
    ts_event
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00115000    NaN    NaN    NaN    NaN       0
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00155000    NaN    NaN    NaN    NaN       0
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00170000    NaN    NaN    NaN    NaN       0
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00195000    NaN    NaN    NaN    NaN       0
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00200000    NaN    NaN    NaN    NaN       0
    ...                                 ...                    ...    ...    ...    ...    ...     ...
    2024-12-17 15:59:00-05:00            61  AAPL  261218C00240000  50.70  50.70  50.70  50.70       0
    2024-12-17 15:59:00-05:00            61  AAPL  261218P00050000   0.29   0.29   0.29   0.29       0
    2024-12-17 15:59:00-05:00            61  AAPL  270115C00240000  50.14  50.14  50.14  50.14       0
    2024-12-17 15:59:00-05:00            61  AAPL  270115C00250000  44.34  44.34  44.34  44.34       0
    2024-12-17 15:59:00-05:00            61  AAPL  270115C00440000   3.50   3.50   3.50   3.50       0
    
    [3129360 rows x 7 columns]
                               publisher_id                 symbol   open   high    low  close  volume
    ts_event
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00220000  30.70  30.70  30.70  30.70       1
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00225000  25.95  25.95  25.95  25.95       2
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00242500   8.20   8.20   8.20   8.20       1
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00245000   6.30   6.30   6.17   6.17       4
    2024-12-17 09:30:00-05:00            20  AAPL  241220C00247500   3.70   3.85   3.70   3.85       2
    ...                                 ...                    ...    ...    ...    ...    ...     ...
    2024-12-17 15:55:00-05:00            61  AAPL  250124C00255000   5.95   5.95   5.95   5.95       1
    2024-12-17 15:55:00-05:00            61  AAPL  250124C00285000   0.30   0.30   0.30   0.30      47
    2024-12-17 15:55:00-05:00            61  AAPL  250124P00235000   1.01   1.01   1.01   1.01       1
    2024-12-17 15:55:00-05:00            61  AAPL  250321C00225000  33.90  33.90  33.90  33.90       1
    2024-12-17 15:55:00-05:00            61  AAPL  250919P00105000   0.33   0.33   0.33   0.33       2
    
    [231713 rows x 7 columns]
    

