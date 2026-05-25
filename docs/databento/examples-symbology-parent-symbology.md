Examples and tutorials

Symbology

# Parent
symbology

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Overview![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

In this example we will use the [Historical client](/docs/api-reference-
historical/client) to retrieve a large number of instrument definitions using
[parent symbology](/docs/standards-and-conventions/symbology#parent). When
requesting data with a parent symbol (`ES.FUT`), all contracts that trade
under the parent name will be returned. This includes outright futures
(`ESM5`), as well as calendar spreads (`ESM5-ESU5`).

> ![See also](/docs/assets/images/callout-see-also.3a97bee16e89f8b34bd0.svg)
>
> See also
>
> If you are only interested in the front month contract for a futures
> product, see our [continuous symbology](/docs/examples/symbology/continuous)
> example.

We'll use the [definition schema](/docs/schemas-and-data-formats/instrument-
definitions), which contains instrument definitions and properties such as
`expiration`, `raw_symbol`, and `instrument_class`. `instrument_class` will
indicate the type of instrument, such as future or future spread. A full list
of variants can be found in the [instrument class
documentation](/docs/schemas-and-data-formats/instrument-
definitions#instrument-class).

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Example![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
    import databento as db
    
    # Create a historical client
    client = db.Historical("$YOUR_API_KEY")
    
    # Request definition data
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        start="2025-05-19",
        symbols="ES.FUT",
        stype_in="parent",
        schema="definition",
    )
    
    # Convert to DataFrame
    df = data.to_df()
    
    # Filter out spreads and sort by expiration
    df = df[df["instrument_class"] == db.InstrumentClass.FUTURE]
    df = df.set_index("expiration").sort_index()
    
    print(df[["instrument_id", "raw_symbol"]])
    

## ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Result![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Python

![](/docs/assets/images/code-snippet/dropdown-icon.87a5d291e2af70b37588.svg)

[Python](javascript:void\(0\))[C++](javascript:void\(0\))[Rust](javascript:void\(0\))

![](/docs/assets/images/copy-icon.db4b3a58116c828f2c56.svg)

    
    
                               instrument_id raw_symbol
    expiration
    2025-06-20 13:30:00+00:00           4916       ESM5
    2025-09-19 13:30:00+00:00          14160       ESU5
    2025-12-19 14:30:00+00:00         294973       ESZ5
    2026-03-20 13:30:00+00:00       42140878       ESH6
    2026-06-18 13:30:00+00:00       42140864       ESM6
    ...
    2026-09-18 13:30:00+00:00       42140870       ESU6
    2026-12-18 14:30:00+00:00          10252       ESZ6
    2027-03-19 13:30:00+00:00       42140860       ESH7
    2027-06-17 13:30:00+00:00       42140856       ESM7
    2027-09-17 13:30:00+00:00       42140874       ESU7
    2027-12-17 14:30:00+00:00          17740       ESZ7
    2028-03-17 13:30:00+00:00       42140879       ESH8
    

