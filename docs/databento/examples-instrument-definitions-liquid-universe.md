Examples and tutorials

Instrument definitions

# Finding liquid
instruments

## What is a liquid
instrument?

A liquid instrument is an instrument that is readily traded. Identifying
liquid instruments is important for a few reasons:

  * **Reducing transaction costs.** A liquid instrument generally has tight bid-ask spreads and adequate size on the bid and ask. This reduces the amount of slippage incurred from using aggressive order types.
  * **Signal generation.** Consistent trading activity allows for signal generation in order flow strategies.
  * **Price efficiency.** Increased participation results in more efficient price discovery. This results in more reliable price valuations for risk management.

Check out the [Databento Microstructure
Guide](https://databento.com/microstructure/liquidity) for more information
about liquidity.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In this example we'll use the [Historical client](/docs/api-reference-
historical/client/historical) to find liquid futures instruments.

We'll use the following schemas:

  * The [statistics schema](/docs/schemas-and-data-formats/statistics), which contains exchange-published statistics such as cleared volume and open interest.
  * The [definition schema](/docs/schemas-and-data-formats/instrument-definitions), which contains instrument definitions and properties such as `raw_symbol` and `asset`. The `asset` field refers to the parent product for an instrument.
  * The [BBO schema](/docs/schemas-and-data-formats/bbo), which contains the best bid and offer, subsampled at 1-second or 1-minute intervals.

We'll request statistics and definition data for all symbols. Next, we'll
filter for the top 25 instruments by volume. After we filter, we'll get BBO
data for these instruments to find the median bid and ask size over a full
day.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Example![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    # First, create a Historical client
    client = db.Historical("$YOUR_API_KEY")
    
    # Set parameters
    dataset = "GLBX.MDP3"
    start_date = "2025-03-07"
    top_instruments_count = 25
    
    # First, download definition data for all symbols
    def_data = client.timeseries.get_range(
        dataset=dataset,
        symbols="ALL_SYMBOLS",
        schema="definition",
        start=start_date,
    )
    
    # Convert to DataFrame. Filter for outright futures
    def_df = def_data.to_df()
    def_df = def_df[def_df["instrument_class"] == db.InstrumentClass.FUTURE]
    def_df = def_df[["raw_symbol", "instrument_id", "asset", "min_price_increment"]]
    
    # Next, download statistics data for all symbols
    stats_data = client.timeseries.get_range(
        dataset=dataset,
        symbols="ALL_SYMBOLS",
        schema="statistics",
        start=start_date,
    )
    
    # Convert to DataFrame
    stats_df = stats_data.to_df()
    
    # Get cleared volume records
    volume_df = stats_df[stats_df["stat_type"] == db.StatType.CLEARED_VOLUME]
    volume_df = volume_df.drop_duplicates("instrument_id", keep="last")
    volume_df = volume_df.rename(columns={"quantity": "volume"})
    volume_df = volume_df[["instrument_id", "volume"]]
    
    # Get open interest records
    open_interest_df = stats_df[stats_df["stat_type"] == db.StatType.OPEN_INTEREST]
    open_interest_df = open_interest_df.drop_duplicates("instrument_id", keep="last")
    open_interest_df = open_interest_df.rename(columns={"quantity": "open_interest"})
    open_interest_df = open_interest_df[["instrument_id", "open_interest"]]
    
    # Merge volume and open interest data
    stats_df = volume_df.merge(open_interest_df, on="instrument_id", how="inner")
    
    # Merge definition data with statistics data
    stats_df = stats_df.merge(def_df, on="instrument_id", how="inner")
    
    # Sort by volume, keeping one instrument per product
    stats_df = stats_df.sort_values("volume", ascending=False)
    stats_df = stats_df.drop_duplicates("asset")
    
    # Get instrument IDs for highest volume instruments
    top_instruments = stats_df["instrument_id"].to_list()[:top_instruments_count]
    
    # Download BBO-1s data for highest volume instruments
    bbo_data = client.timeseries.get_range(
        dataset=dataset,
        symbols=top_instruments,
        stype_in="instrument_id",
        schema="bbo-1s",
        start=start_date,
    )
    
    # Convert to DataFrame
    bbo_df = bbo_data.to_df()
    
    # Merge DataFrames
    df = bbo_df.merge(stats_df, on="instrument_id", how="inner")
    df["spread_ticks"] = (df["ask_px_00"] - df["bid_px_00"]) / df["min_price_increment"]
    
    # Calculate aggregated values and sort by volume
    df = (
        df.groupby(by="instrument_id")
        .agg(
            product=("asset", "first"),
            symbol=("raw_symbol", "first"),
            volume=("volume", "first"),
            open_interest=("open_interest", "first"),
            median_bid_size=("bid_sz_00", lambda x: int(x.median())),
            median_ask_size=("ask_sz_00", lambda x: int(x.median())),
            median_tick_spread=("spread_ticks", lambda x: int(x.median().round())),
        )
        .sort_values("volume", ascending=False)
    )
    
    print(df)
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Result![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                  product symbol   volume  open_interest  median_bid_size  median_ask_size  median_tick_spread
    instrument_id
    42004113           ZN   ZNM5  3259518        4675273              903              946                   1
    5002               ES   ESH5  2553819        2115168                9                9                   1
    42003617          MNQ  MNQH5  2419630         154823                2                2                   2
    42325990           ZF   ZFM5  2039292        6111849              483              492                   1
    42005347          MES  MESH5  1931181         210864               12               12                   1
    42325992           ZT   ZTM5  1320314        3854308              443              455                   1
    42002878           TN   TNM5   962958        2206335              278              285                   1
    254274            SR3  SR3Z5   894673         979670             2864             2898                   1
    42288528           NQ   NQH5   763150         273526                1                1                   4
    42004255           ZB   ZBM5   708447        1770658              228              236                   1
    42001682           UB   UBM5   474088        1757409               99              102                   1
    42272              6E   6EH5   392404         601400               15               14                   1
    625061             CL   CLJ5   341632         239110                6                6                   1
    680969             ZC   ZCK5   313858         756635               83               74                   1
    42001620          RTY  RTYH5   258273         454862                2                3                   2
    57969              6J   6JH5   242466         278473               23               21                   1
    42011026          MYM  MYMH5   216632          23321                3                3                   2
    892                NG   NGJ5   202475         208149                3                3                   2
    457556             ZS   ZSK5   192141         382968               16               15                   1
    42002868           YM   YMH5   185559          70792                3                3                   2
    29307              6B   6BH5   178634         192142               39               40                   1
    19604              GC   GCJ5   166364         329381                2                2                   2
    713217             ZQ   ZQJ5   158838         497521            41383            11147                   1
    45908              6C   6CH5   137522         303329               27               28                   1
    52126              6A   6AH5   131315         180048               29               31                   1
    

