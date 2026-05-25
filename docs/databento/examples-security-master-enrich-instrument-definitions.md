Examples and tutorials

Security master

# Enrich instrument
definitions

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

This example demonstrates how to merge security master data with the
[Definition schema](/docs/schemas-and-data-formats/instrument-definitions)
using the [Historical client](/docs/api-reference-historical/client). This
gives you the benefit of multiple symbology identifiers and additional
security and listing information included with each record.

For our purposes we'll assume the latest definition schema can be obtained on
2024-04-01.

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> [Security master](/docs/venues-and-datasets/security-master) dataset guide
> for further details.

## Instrument
identification and symbology![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

When identifying a listing on a specific venue, we suggest using the
`nasdaq_symbol` field, as it aligns with the normalized `raw_symbol` for
instruments. It's also important to correctly match the venue, being the
`operating_mic` ISO 10838 Market Identifier Code (MIC) with the definitions
`exchange` field.

By utilizing the
[ISIN](https://en.wikipedia.org/wiki/International_Securities_Identification_Number),
you can more precisely identify the security within the security master and
corporate actions datasets. When querying these datasets, specifying the
`isin` symbology type ensures the most accurate results.

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    import pandas as pd
    
    # Create a historical client
    hist_client = db.Historical(key="$YOUR_API_KEY")
    
    # Request the instrument definitions
    data = hist_client.timeseries.get_range(
        dataset="XNAS.ITCH",
        symbols=["AAPL"],
        start="2024-04-01",
        limit=1,
    )
    df_definition = data.to_df()
    
    # Create a reference client
    ref_client = db.Reference(key="$YOUR_API_KEY")
    
    # Request the latest security master
    df_security_master = ref_client.security_master.get_last(
        symbols=["AAPL"],
        countries=["US"],
    )
    
    # Merge the dataframes by joining the definition `symbol` with the security master `symbol`
    df_combined = pd.merge(
        df_definition,
        df_security_master,
        left_on="symbol",
        right_on="symbol",
        how="inner",
    )
    
    # Display the combined records
    print(df_combined.head())
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Result![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                                 ts_event  rtype  publisher_id  instrument_id action side  depth   price  size  flags  ts_in_delta  sequence symbol                 ts_record listing_id listing_group_id security_id issuer_id listing_status listing_source listing_created_date listing_date delisting_date issuer_name security_type security_description primary_exchange exchange operating_mic nasdaq_symbol local_code          isin   us_code   bbg_comp_id bbg_comp_ticker          figi figi_ticker  fisn                   lei   sic     cik  gics   naics   cic     cfi incorporation_country listing_country register_country trading_currency  multi_currency segment_mic_name segment_mic  structure  lot_size  par_value par_value_currency voting  vote_per_sec  shares_outstanding shares_outstanding_date                       ts_created
    0 2024-04-01 08:00:05.048097430+00:00      0             2             38      T    A      0  171.85     1    130       169598    280962   AAPL 2024-08-05 02:41:52+00:00   L-135825         LG-35825     S-33449   I-30017              L              M           2001-05-05   1980-12-12           None   Apple Inc           EQS      Ordinary Shares           USNASD   USNASD          XNAS          AAPL       AAPL  US0378331005  37833100  BBG000B9XRY4         AAPL US  BBG000B9Y5X2     AAPL UW   NaN  HWUPKR0MPOU8FGXBT394  3571  320193   NaN  334111  US31  ESVUFR                    US              US               US              USD           False    Global Select        XNGS        NaN       100    0.00001                USD      V             1         15204137000              2024-07-19 2024-11-01 03:02:18.020386+00:00
    

