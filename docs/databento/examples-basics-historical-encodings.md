Examples and tutorials

Historical data

# Convert DBN to
other encoding formats![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In this example, we’ll walk through how to convert DBN to CSV, JSON, or
Parquet encoding formats.

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)DBN![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

[DBN](/docs/standards-and-conventions/databento-binary-encoding) is an
extremely fast message encoding and storage format for normalized market data.
DBN is the default encoding used across our API and client libraries.

Historical data can be requested with [timeseries.get_range()](/docs/api-
reference-historical/timeseries/timeseries-get-range). This method returns a
[DBNStore](/docs/api-reference-historical/helpers/dbn-store).

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> Historical data can also be requested in various encoding formats with batch
> downloads. See the [programmatic batch downloads
> example](/docs/examples/basics-historical/programmatic-batch-download) for
> more information.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    client = db.Historical("YOUR_API_KEY")
    
    dbn_store = client.timeseries.get_range(
        dataset="EQUS.SUMMARY",
        symbols=["AAPL", "NVDA", "NFLX", "META", "GOOGL"],
        schema="ohlcv-1d",
        start="2025-09-30",
    )
    

You can save this data to a DBN file with [DBNStore.to_file()](/docs/api-
reference-historical/helpers/dbn-store-to-file), then read it back into a
DBNStore with [DBNStore.from_file()](/docs/api-reference-
historical/helpers/dbn-store-from-file).

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store.to_file("demo_data.dbn")
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    

You can inspect the symbology mappings for a [DBNStore](/docs/api-reference-
historical/helpers/dbn-store) with the `symbology` attribute.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    
    print(dbn_store.symbology)
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    {"symbols": ["AAPL", "NVDA", "NFLX", "META", "GOOGL"],
     "stype_in": "raw_symbol",
     "stype_out": "instrument_id",
     "start_date": "2025-09-30",
     "end_date": "2025-10-01",
     "partial": [],
     "not_found": [],
     "mappings": {"GOOGL": [{"start_date": datetime.date(2025, 9, 30),
                             "end_date": datetime.date(2025, 10, 1),
                             "symbol": "7152"}],
                  "NVDA": [{"start_date": datetime.date(2025, 9, 30),
                            "end_date": datetime.date(2025, 10, 1),
                            "symbol": "11667"}],
                  "AAPL": [{"start_date": datetime.date(2025, 9, 30),
                            "end_date": datetime.date(2025, 10, 1),
                            "symbol": "38"}],
                  "NFLX": [{"start_date": datetime.date(2025, 9, 30),
                            "end_date": datetime.date(2025, 10, 1),
                            "symbol": "11275"}],
                  "META": [{"start_date": datetime.date(2025, 9, 30),
                            "end_date": datetime.date(2025, 10, 1),
                            "symbol": "10451"}]}}
    

You can [iterate](/docs/api-reference-historical/helpers/dbn-store-iter) over
a DBNStore, which will yield DBN records.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    
    for record in dbn_store:
        print(record)
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    OhlcvMsg(rtype=<RType.OHLCV_1D: 35>, publisher_id=EQUS.SUMMARY.EQUS (90), instrument_id=38, ts_event=1759190400000000000, pretty_ts_event='2025-09-30T00:00:00.000000000Z', open=254855000000, pretty_open=254.855, high=255919000000, pretty_high=255.919, low=253110000000, pretty_low=253.11, close=254630000000, pretty_close=254.63, volume=37704259)
    OhlcvMsg(rtype=<RType.OHLCV_1D: 35>, publisher_id=EQUS.SUMMARY.EQUS (90), instrument_id=7152, ts_event=1759190400000000000, pretty_ts_event='2025-09-30T00:00:00.000000000Z', open=242810000000, pretty_open=242.81, high=243290000000, pretty_high=243.29, low=239245000000, pretty_low=239.245, close=243100000000, pretty_close=243.1, volume=34724346)
    OhlcvMsg(rtype=<RType.OHLCV_1D: 35>, publisher_id=EQUS.SUMMARY.EQUS (90), instrument_id=10451, ts_event=1759190400000000000, pretty_ts_event='2025-09-30T00:00:00.000000000Z', open=742250000000, pretty_open=742.25, high=742970000000, pretty_high=742.97, low=726300000000, pretty_low=726.3, close=734380000000, pretty_close=734.38, volume=16226750)
    OhlcvMsg(rtype=<RType.OHLCV_1D: 35>, publisher_id=EQUS.SUMMARY.EQUS (90), instrument_id=11275, ts_event=1759190400000000000, pretty_ts_event='2025-09-30T00:00:00.000000000Z', open=1206410000000, pretty_open=1206.41, high=1208500000000, pretty_high=1208.5, low=1178000000000, pretty_low=1178, close=1198920000000, pretty_close=1198.92, volume=3830304)
    OhlcvMsg(rtype=<RType.OHLCV_1D: 35>, publisher_id=EQUS.SUMMARY.EQUS (90), instrument_id=11667, ts_event=1759190400000000000, pretty_ts_event='2025-09-30T00:00:00.000000000Z', open=182080000000, pretty_open=182.08, high=187350000000, pretty_high=187.35, low=181480000000, pretty_low=181.48, close=186580000000, pretty_close=186.58, volume=236981032)
    

You can convert a DBNStore into a Pandas DataFrame with
[DBNStore.to_df()](/docs/api-reference-historical/helpers/dbn-store-to-df).

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    
    df = dbn_store.to_df()
    print(df)
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                               rtype  publisher_id  instrument_id      open      high       low    close     volume symbol
    ts_event
    2025-09-30 00:00:00+00:00     35            90             38   254.855   255.919   253.110   254.63   37704259   AAPL
    2025-09-30 00:00:00+00:00     35            90           7152   242.810   243.290   239.245   243.10   34724346  GOOGL
    2025-09-30 00:00:00+00:00     35            90          10451   742.250   742.970   726.300   734.38   16226750   META
    2025-09-30 00:00:00+00:00     35            90          11275  1206.410  1208.500  1178.000  1198.92    3830304   NFLX
    2025-09-30 00:00:00+00:00     35            90          11667   182.080   187.350   181.480   186.58  236981032   NVDA
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)CSV![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> Data can also be directly requested in CSV format by setting
> `encoding="csv"` in [batch.submit_job()](/docs/api-reference-
> historical/batch/batch-submit-job).

You can save a DBNStore to a CSV file with [DBNStore.to_csv()](/docs/api-
reference-historical/helpers/dbn-store-to-csv).

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    
    dbn_store.to_csv("demo_data.csv")
    
    with open("demo_data.csv", "r") as f:
        print(f.read())
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol
    2025-09-30T00:00:00.000000000Z,35,90,38,254.855000000,255.919000000,253.110000000,254.630000000,37704259,AAPL
    2025-09-30T00:00:00.000000000Z,35,90,7152,242.810000000,243.290000000,239.245000000,243.100000000,34724346,GOOGL
    2025-09-30T00:00:00.000000000Z,35,90,10451,742.250000000,742.970000000,726.300000000,734.380000000,16226750,META
    2025-09-30T00:00:00.000000000Z,35,90,11275,1206.410000000,1208.500000000,1178.000000000,1198.920000000,3830304,NFLX
    2025-09-30T00:00:00.000000000Z,35,90,11667,182.080000000,187.350000000,181.480000000,186.580000000,236981032,NVDA
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)JSON![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> Data can also be directly requested in JSON format by setting
> `encoding="json"` in [batch.submit_job()](/docs/api-reference-
> historical/batch/batch-submit-job).

You can save a DBNStore to a JSONL file with [DBNStore.to_json()](/docs/api-
reference-historical/helpers/dbn-store-to-json).

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    
    dbn_store.to_json("demo_data.json")
    
    with open("demo_data.json", "r") as f:
        print(f.read())
    

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    {"hd":{"ts_event":"2025-09-30T00:00:00.000000000Z","rtype":35,"publisher_id":90,"instrument_id":38},"open":"254.855000000","high":"255.919000000","low":"253.110000000","close":"254.630000000","volume":"37704259","symbol":"AAPL"}
    {"hd":{"ts_event":"2025-09-30T00:00:00.000000000Z","rtype":35,"publisher_id":90,"instrument_id":7152},"open":"242.810000000","high":"243.290000000","low":"239.245000000","close":"243.100000000","volume":"34724346","symbol":"GOOGL"}
    {"hd":{"ts_event":"2025-09-30T00:00:00.000000000Z","rtype":35,"publisher_id":90,"instrument_id":10451},"open":"742.250000000","high":"742.970000000","low":"726.300000000","close":"734.380000000","volume":"16226750","symbol":"META"}
    {"hd":{"ts_event":"2025-09-30T00:00:00.000000000Z","rtype":35,"publisher_id":90,"instrument_id":11275},"open":"1206.410000000","high":"1208.500000000","low":"1178.000000000","close":"1198.920000000","volume":"3830304","symbol":"NFLX"}
    {"hd":{"ts_event":"2025-09-30T00:00:00.000000000Z","rtype":35,"publisher_id":90,"instrument_id":11667},"open":"182.080000000","high":"187.350000000","low":"181.480000000","close":"186.580000000","volume":"236981032","symbol":"NVDA"}
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parquet![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

You can save a DBNStore to a parquet file with
[DBNStore.to_parquet()](/docs/api-reference-historical/helpers/dbn-store-to-
parquet).

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    dbn_store = db.DBNStore.from_file("demo_data.dbn")
    
    dbn_store.to_parquet("demo_data.parquet")
    

